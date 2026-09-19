"""
handlers/alert_handler.py
- Alerts persisted to /data/active_alerts.json (Railway Volume)
- asyncio.Lock per alert prevents double-assignment race condition
- Callback auth: only ADMINS can action buttons
"""

import asyncio
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

from shift_manager import get_on_shift_admins, get_all_admins
from storage.case_store import (
    create_case, assign_case, get_case,
    save_active_alerts, load_active_alerts,
    set_report_msg_id,
)
from storage.user_store import is_authorized


def _esc(t: str) -> str:
    """Escape Markdown v1 special chars in dynamic content."""
    return str(t).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


logger           = logging.getLogger(__name__)
TRIGGER_WORDS    = ['#maintenance', '#repairs', '#repair']

# Anti-spam cooldown per driver: after a #repairs/#maintenance message opens
# a case, the same driver is throttled for a random 5-7 minute window so
# repeated posts don't flood agents with duplicate cases. The exact duration
# is randomized (rather than a fixed value) so drivers can't easily learn and
# game a predictable window.
COOLDOWN_MIN_SECONDS = 5 * 60
COOLDOWN_MAX_SECONDS = 6 * 60

# If a driver keeps spamming while their case is still unassigned, agents get
# nudged again — but not on every single spam message, or the nudge itself
# becomes spam. This throttles the nudges independently of the case cooldown.
UNASSIGNED_NUDGE_COOLDOWN_SECONDS = 2 * 60


class AlertHandler:
    def __init__(self):
        self._alerts: dict[str, dict]        = {}
        self._locks:  dict[str, asyncio.Lock] = {}
        self._driver_last_time: dict[int, datetime] = {}
        self._driver_cooldown_until: dict[int, datetime] = {}
        self._driver_last_alert: dict[int, str] = {}
        self._last_nudge_at: dict[str, datetime] = {}
        self._short_map: dict[str, str]       = {}

    # ── Persistence ───────────────────────────────────────────────────────────

    def load_from_disk(self):
        """Reload active alerts and discard records for already-closed cases."""
        raw = load_active_alerts()
        self._alerts.clear()
        self._short_map.clear()
        for aid, record in raw.items():
            case = get_case(aid)
            if case and case.get("status") == "done":
                continue
            self._alerts[aid] = record
            self._short_map[aid.replace("-", "")[:12]] = aid
        if len(self._alerts) != len(raw):
            self._persist()
        logger.info(f"Loaded {len(self._alerts)} active alerts from disk")

    def _persist(self):
        save_active_alerts(self._alerts)

    async def _persist_async(self):
        # JSON serialization and fsync can be slow on a mounted volume. Keep it
        # off the Telegram update loop so commands and callbacks stay responsive.
        await asyncio.to_thread(self._persist)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_kb(self, alert_id: str) -> InlineKeyboardMarkup:
    # Telegram callback_data limit is 64 bytes — a UUID is 36 chars, fine
        return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Assign", callback_data=f"assign|{alert_id}"),
        InlineKeyboardButton("🚫 Ignore", callback_data=f"ignore|{alert_id}"),
    ]])

    def _register_alert(self, alert_id: str) -> str:
    # Keep short_map for backwards compat with old buttons, but new ones use full id
        short_id = alert_id.replace("-", "")[:12]
        self._short_map[short_id] = alert_id
        return alert_id  # ← return full id now

    def _resolve(self, token: str):
    # Try as full alert_id first
        if token in self._alerts:
            return token, self._alerts[token]
    # Fall back to short_map for older buttons
        alert_id = self._short_map.get(token, token)
        return alert_id, self._alerts.get(alert_id)

    async def _restore_from_case(self, case_id: str):
        """Rebuild callback state when active_alerts.json is missing or stale."""
        case = await asyncio.to_thread(get_case, case_id)
        if not case or case.get("status") == "done":
            return case_id, None
        record = {
            "alert_id": case_id,
            "recipients": {},
            "taken_by": (
                [case.get("agent_id"), case.get("agent_name")]
                if case.get("agent_id") is not None else None
            ),
            "created_at": case.get("opened_at") or datetime.now(timezone.utc).isoformat(),
            "last_escalated_at": None,
            "escalation_count": 0,
            "driver_id": 0,
            "driver_name": case.get("driver_name") or "Unknown",
            "driver_username": case.get("driver_username"),
            "group_name": case.get("group_name") or "Driver Group",
            "text": case.get("description") or "",
        }
        self._alerts[case_id] = record
        self._register_alert(case_id)
        await self._persist_async()
        logger.info(f"Restored alert state for case {case_id}")
        return case_id, record
    
    def _get_lock(self, alert_id: str) -> asyncio.Lock:
        if alert_id not in self._locks:
            self._locks[alert_id] = asyncio.Lock()
        return self._locks[alert_id]

    @staticmethod
    def _log_background_result(task):
        if task.cancelled():
            return
        error = task.exception()
        if error:
            logger.error(
                f"Background assignment update failed: {error}",
                exc_info=(type(error), error, error.__traceback__),
            )

    # ── Group trigger handler ─────────────────────────────────────────────────

    async def handle(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        import re
        msg = update.effective_message
        if not msg:
            return

        # Messages sent "as the group" (anonymous admin) or from a linked
        # channel arrive with sender_chat set instead of a normal user, and
        # Telegram attributes them to the GroupAnonymousBot / ChannelBot
        # system account (which has is_bot=True). Don't drop those — treat
        # the group/channel itself as the reporter.
        is_anonymous = msg.sender_chat is not None
        if not is_anonymous and (not update.effective_user or update.effective_user.is_bot):
            return

        text  = msg.text or msg.caption or ""
        photo = msg.photo[-1] if msg.photo else None

        def _match(word, hay):
            if word.startswith('#'):
                return word.lower() in hay.lower()
            return bool(re.search(r'\b' + re.escape(word) + r'\b', hay, re.IGNORECASE))

        if not any(_match(w, text) for w in TRIGGER_WORDS):
            return

        if is_anonymous:
            driver_id = msg.sender_chat.id
        else:
            driver_id = update.effective_user.id
        now = datetime.now(timezone.utc)

        cooldown_until = self._driver_cooldown_until.get(driver_id)
        if cooldown_until:
            if isinstance(cooldown_until, str):
                cooldown_until = datetime.fromisoformat(cooldown_until)
            if cooldown_until.tzinfo is None:
                cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
            if now < cooldown_until:
                # Driver is spamming #repairs/#maintenance within the cooldown
                # window. Don't open a duplicate case for it — but if their
                # last case is still sitting unassigned, that's a signal
                # worth escalating to agents (throttled separately below).
                await self._nudge_if_unassigned(driver_id, ctx)
                return

        self._driver_last_time[driver_id] = now
        self._driver_cooldown_until[driver_id] = now + timedelta(
            seconds=random.uniform(COOLDOWN_MIN_SECONDS, COOLDOWN_MAX_SECONDS)
        )

        chat_title = update.effective_chat.title or "Driver Group"
        if is_anonymous:
            driver_name     = msg.sender_chat.title or chat_title
            driver_username = None
        else:
            user            = update.effective_user
            driver_name     = f"{user.first_name} {user.last_name or ''}".strip()
            driver_username = user.username or None
        alert_id = str(uuid.uuid4())

        self._alerts[alert_id] = {
            "alert_id":           alert_id,
            "recipients":         {},
            "taken_by":           None,
            "created_at":         now.isoformat(),
            "last_escalated_at":  None,
            "escalation_count":   0,
            "driver_id":          driver_id,
            "driver_name":        driver_name,
            "driver_username":    driver_username,
            "group_name":         chat_title,
            "text":               text,
        }

        await asyncio.to_thread(
            create_case,
            case_id=alert_id,
            driver_name=driver_name,
            driver_username=driver_username,
            group_name=chat_title,
            description=text,
        )
        self._driver_last_alert[driver_id] = alert_id

        short_id   = self._register_alert(alert_id)
        kb         = self._make_kb(short_id)
        recipients = get_on_shift_admins() or get_all_admins()
        notified   = 0
        dm_text    = (
            "🔔 You have been mentioned in *" + _esc(chat_title) + "*\n\n"
            "👤 *Reported by:* " + _esc(driver_name) + "\n"
            "📝 *Issue:* " + _esc(text[:200])
        )

        async def notify_admin(admin):
            try:
                if photo:
                    sent = await ctx.bot.send_photo(
                        admin["id"], photo=photo.file_id,
                        caption=dm_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
                    )
                else:
                    sent = await ctx.bot.send_message(
                        admin["id"], dm_text,
                        parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
                    )
                self._alerts[alert_id]["recipients"].setdefault(admin["id"], []).append(sent.message_id)
                return True
            except TelegramError as e:
                logger.warning(f"Could not DM admin {admin['id']}: {e}")
                return False

        if recipients:
            results = await asyncio.gather(*(notify_admin(a) for a in recipients), return_exceptions=True)
            notified = sum(result is True for result in results)

        await self._persist_async()
        if notified == 0:
            logger.warning("No admins could be reached for alert!")

    async def _nudge_if_unassigned(self, driver_id: int, ctx: ContextTypes.DEFAULT_TYPE):
        """A driver spammed #repairs again while still on cooldown. If their
        most recent case is still unassigned, ping agents again so it doesn't
        get lost — throttled so this can't itself turn into spam."""
        alert_id = self._driver_last_alert.get(driver_id)
        if not alert_id:
            return
        record = self._alerts.get(alert_id)
        if not record or record.get("taken_by") is not None:
            return  # already assigned (or already gone) — nothing to nudge about

        now = datetime.now(timezone.utc)
        last_nudge = self._last_nudge_at.get(alert_id)
        if last_nudge and (now - last_nudge).total_seconds() < UNASSIGNED_NUDGE_COOLDOWN_SECONDS:
            return
        self._last_nudge_at[alert_id] = now

        short_id = self._register_alert(alert_id)
        kb       = self._make_kb(short_id)
        text     = (
            "⚠️ *Case still unassigned!*\n\n"
            f"👤 *Driver:* {_esc(record.get('driver_name', '—'))} keeps reporting "
            "this issue and no one has picked it up yet.\n"
            f"📌 *Group:* {_esc(record.get('group_name', '—'))}\n"
            f"📝 *Issue:* {_esc((record.get('text') or '—')[:200])}\n\n"
            "Please assign it."
        )

        recipients = get_on_shift_admins() or get_all_admins()
        async def send_nudge(admin):
            try:
                sent = await ctx.bot.send_message(
                    admin["id"], text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
                )
                record["recipients"].setdefault(admin["id"], []).append(sent.message_id)
            except TelegramError as e:
                logger.warning(f"Could not nudge admin {admin['id']} about unassigned case: {e}")

        if recipients:
            await asyncio.gather(*(send_nudge(a) for a in recipients), return_exceptions=True)

        await self._persist_async()

    # ── Assignment ────────────────────────────────────────────────────────────

    async def _replace_alert_message(self, query, text: str):
        """Remove alert buttons from both text and photo notifications."""
        try:
            if query.message and query.message.caption is not None:
                await query.edit_message_caption(caption=text, reply_markup=None)
            else:
                await query.edit_message_text(text, reply_markup=None)
        except TelegramError as e:
            logger.warning(f"Could not update alert message: {e}")

    async def _cleanup_assignment_messages(self, record, admin_id: int, name: str, ctx):
        """Update old alert DMs concurrently so they cannot freeze polling."""
        done_text = f"✅ Case assigned to {_esc(name)}.\nNo action needed."

        async def clean_one(chat_id, message_id):
            try:
                chat_id = int(chat_id)
                if chat_id == admin_id:
                    await ctx.bot.delete_message(chat_id=chat_id, message_id=message_id)
                    return
                try:
                    await ctx.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=done_text, reply_markup=None,
                    )
                except TelegramError:
                    await ctx.bot.edit_message_caption(
                        chat_id=chat_id, message_id=message_id,
                        caption=done_text, reply_markup=None,
                    )
            except (TelegramError, ValueError, TypeError):
                pass

        tasks = [
            clean_one(chat_id, message_id)
            for chat_id, message_ids in record.get("recipients", {}).items()
            for message_id in message_ids
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _post_assignment_updates(self, record, admin, name, alert_id, prev_agent_id, ctx):
        """Run non-critical Telegram updates after the case is safely claimed."""
        await self._cleanup_assignment_messages(record, admin.id, name, ctx)

        if prev_agent_id and prev_agent_id != admin.id:
            try:
                await ctx.bot.send_message(
                    prev_agent_id,
                    f"🔁 *Case taken over by {_esc(name)}*\n\n"
                    f"The case you reassigned has been accepted.\n"
                    f"It has been removed from your active cases.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError as e:
                logger.warning(f"Could not notify previous agent for {alert_id}: {e}")

        from config import config as cfg
        from shifts import MAIN_ADMIN_ID
        dest_id = cfg.REPORTS_GROUP_ID or next(iter(MAIN_ADMIN_ID), None)
        if not dest_id:
            return

        created_at = record.get("created_at")
        try:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            created_at = None
        secs = int((datetime.now(timezone.utc) - created_at).total_seconds()) if created_at else 0
        action = "Reassigned" if prev_agent_id else "Assigned"
        report_text = (
            f"✅ *Case {action}*\n\n"
            f"📌 *Group:* {_esc(record.get('group_name', '—'))}\n"
            f"👤 *Reported by:* {_esc(record.get('driver_name', '—'))}\n"
            f"🙋 *Handled by:* {_esc(name)}\n"
            f"⏱ *Response:* {secs}s\n"
            f"📝 {_esc(record.get('text', '(no details)')[:200])}"
        )
        try:
            sent = await ctx.bot.send_message(dest_id, report_text, parse_mode=ParseMode.MARKDOWN)
            await asyncio.to_thread(set_report_msg_id, alert_id, sent.message_id)
        except TelegramError as e:
            logger.warning(f"Could not post assignment to reports for {alert_id}: {e}")

    async def _do_assign(self, admin, name, alert_id, record, ctx):
        lock = self._get_lock(alert_id)
        async with lock:
            previous = record.get("taken_by")
            prev_agent_id = previous[0] if previous else None
            allow_reassign = bool(record.get("reassign_requested"))
            claimed = await asyncio.to_thread(
                assign_case,
                case_id=alert_id,
                agent_id=admin.id,
                agent_name=name,
                agent_username=admin.username,
                allow_reassign=allow_reassign,
            )
            if not claimed:
                logger.info(f"Assignment rejected for {alert_id}; already owned or closed")
                return False
            record["taken_by"] = [admin.id, name]
            record.pop("reassign_requested", None)
            await self._persist_async()

        logger.info(f"Assignment callback completed for {alert_id} by {admin.id}")
        task = asyncio.create_task(
            self._post_assignment_updates(dict(record), admin, name, alert_id, prev_agent_id, ctx)
        )
        task.add_done_callback(self._log_background_result)
        return True

    async def handle_assignment(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query  = update.callback_query
        admin  = update.effective_user

        if not is_authorized(admin.id):
            await query.answer("Not authorized.", show_alert=True)
            return
        await query.answer("Processing...")

        name     = f"{admin.first_name} {admin.last_name or ''}".strip()
        parts    = query.data.split("|")
        action   = parts[0]
        short_id = parts[1] if len(parts) > 1 else ""
        logger.info(f"Assignment callback received: action={action}, case={short_id}, user={admin.id}")

        alert_id, record = self._resolve(short_id)

        # If not in memory, try reloading from disk (happens after bot restart)
        if not record:
            self.load_from_disk()
            alert_id, record = self._resolve(short_id)

        if not record:
            alert_id, record = await self._restore_from_case(alert_id)

        if not record:
            # Truly gone — already assigned and cleaned up
            await self._replace_alert_message(query, "✅ This alert was already handled.")
            return

        if action == "ignore":
            await self._replace_alert_message(
                query, "🚫 You ignored this alert. Another agent can still take it."
            )
            return

        if action in ("assign", "assignrpt"):
            if record.get("taken_by") is not None and not record.get("reassign_requested"):
                if record["taken_by"][0] == admin.id:
                    text = "✅ You already have this case. Use /mycases to manage it."
                else:
                    text = "✅ Already assigned to someone else."
                await self._replace_alert_message(query, text)
                return

            saved = dict(record)
            success = await self._do_assign(admin, name, alert_id, record, ctx)
            if not success:
                await self._replace_alert_message(query, "✅ Already assigned to someone else.")
                return

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            case_text = (
                f"📋 *Active Case*\n\n"
                f"📌 *Group:* {_esc(saved.get('group_name', '—'))}\n"
                f"👤 *Reported by:* {_esc(saved.get('driver_name', '—'))}\n"
                f"📝 *Issue:* {_esc((saved.get('text') or '—')[:200])}"
            )
            case_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Solve",    callback_data=f"close_ask|{alert_id}"),
                InlineKeyboardButton("📋 Report",   callback_data=f"solve|{alert_id}"),
                InlineKeyboardButton("🔁 Reassign", callback_data=f"reassign_{alert_id}"),
            ]])
            try:
                await ctx.bot.send_message(
                    admin.id, case_text,
                    parse_mode=ParseMode.MARKDOWN, reply_markup=case_kb,
                )
            except TelegramError as e:
                logger.error(f"Case {alert_id} was assigned but active-case DM failed: {e}")

    async def handle_reassign(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        admin = update.effective_user
        name  = f"{admin.first_name} {admin.last_name or ''}".strip()

        # Find the alert_id from the case_id in the button that triggered this
        case_id   = query.data.replace("reassign_", "")
        alert_id  = case_id  # alert_id == case_id throughout this bot
        record    = self._alerts.get(alert_id)
        if record is None:
            _, record = await self._restore_from_case(alert_id)

        if record is None:
            await self._replace_alert_message(query, "This case is no longer active.")
            return

        record["reassign_requested"] = True
        await self._persist_async()

        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"🔁 *{_esc(name)}* marked this for reassignment. Notifying other agents...",
            parse_mode=ParseMode.MARKDOWN,
        )

        original = query.message.caption or query.message.text or ""
        dm_text  = (
            f"🔁 *Reassign Request* — {_esc(name)} needs someone to take over:\n\n"
            f"{original[:300]}"
        )

        short_id = self._register_alert(alert_id)
        kb       = self._make_kb(short_id)

        async def notify_agent(a):
            try:
                sent = await ctx.bot.send_message(
                    a["id"], dm_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=kb,
                )
                record["recipients"].setdefault(a["id"], []).append(sent.message_id)
            except TelegramError as e:
                logger.warning(f"Could not send reassignment {alert_id} to {a['id']}: {e}")

        recipients = [a for a in get_all_admins() if a["id"] != admin.id]
        if recipients:
            await asyncio.gather(*(notify_agent(a) for a in recipients), return_exceptions=True)
        await self._persist_async()
        logger.info(f"Reassignment requested for {alert_id} by {admin.id}")
