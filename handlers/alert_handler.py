"""
handlers/alert_handler.py
- Alerts persisted to /data/active_alerts.json (Railway Volume)
- asyncio.Lock per alert prevents double-assignment race condition
- Callback auth: only ADMINS can action buttons
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

from shift_manager import get_on_shift_admins, get_all_admins
from storage.case_store import (
    create_case, assign_case,
    save_active_alerts, load_active_alerts,
    set_report_msg_id,
)
from storage.user_store import is_authorized


def _esc(t: str) -> str:
    """Escape Markdown v1 special chars in dynamic content."""
    return str(t).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


logger           = logging.getLogger(__name__)
TRIGGER_WORDS    = ['#maintenance', '#repairs', '#repair']
COOLDOWN_SECONDS = 10


class AlertHandler:
    def __init__(self):
        self._alerts: dict[str, dict]        = {}
        self._locks:  dict[str, asyncio.Lock] = {}
        self._driver_last_time: dict[int, datetime] = {}
        self._short_map: dict[str, str]       = {}
        self._processed_ai_ids: set           = set()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load_from_disk(self):
        """Call once at startup to reload unassigned alerts."""
        raw = load_active_alerts()
        for aid, record in raw.items():
            if record.get("taken_by"):
                continue
            self._alerts[aid] = record
            self._short_map[aid.replace("-", "")[:12]] = aid
        logger.info(f"Loaded {len(self._alerts)} active alerts from disk")

    def _persist(self):
        save_active_alerts(self._alerts)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_kb(self, short_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Assign", callback_data=f"assign|{short_id}"),
            InlineKeyboardButton("🚫 Ignore", callback_data=f"ignore|{short_id}"),
        ]])

    def _register_alert(self, alert_id: str) -> str:
        short_id = alert_id.replace("-", "")[:12]
        self._short_map[short_id] = alert_id
        return short_id

    def _resolve(self, short_id: str):
        alert_id = self._short_map.get(short_id, short_id)
        return alert_id, self._alerts.get(alert_id)

    def _get_lock(self, alert_id: str) -> asyncio.Lock:
        if alert_id not in self._locks:
            self._locks[alert_id] = asyncio.Lock()
        return self._locks[alert_id]

    # ── Group trigger handler ─────────────────────────────────────────────────

    async def handle(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        import re
        msg = update.effective_message
        if not msg or not update.effective_user or update.effective_user.is_bot:
            return

        text  = msg.text or msg.caption or ""
        photo = msg.photo[-1] if msg.photo else None

        def _match(word, hay):
            if word.startswith('#'):
                return word.lower() in hay.lower()
            return bool(re.search(r'\b' + re.escape(word) + r'\b', hay, re.IGNORECASE))

        if not any(_match(w, text) for w in TRIGGER_WORDS):
            return

        user      = update.effective_user
        driver_id = user.id
        now       = datetime.now(timezone.utc)

        last = self._driver_last_time.get(driver_id)
        if last:
            if isinstance(last, str):
                last = datetime.fromisoformat(last)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < COOLDOWN_SECONDS:
                return

        self._driver_last_time[driver_id] = now

        chat_title  = update.effective_chat.title or "Driver Group"
        driver_name = f"{user.first_name} {user.last_name or ''}".strip()
        alert_id    = str(uuid.uuid4())

        self._alerts[alert_id] = {
            "alert_id":           alert_id,
            "recipients":         {},
            "taken_by":           None,
            "created_at":         now.isoformat(),
            "last_escalated_at":  None,
            "escalation_count":   0,
            "driver_id":          driver_id,
            "driver_name":        driver_name,
            "driver_username":    user.username or None,
            "group_name":         chat_title,
            "text":               text,
        }

        create_case(
            case_id=alert_id,
            driver_name=driver_name,
            driver_username=user.username or None,
            group_name=chat_title,
            description=text,
        )

        short_id   = self._register_alert(alert_id)
        kb         = self._make_kb(short_id)
        recipients = get_on_shift_admins() or get_all_admins()
        notified   = 0
        dm_text    = (
            "🔔 You have been mentioned in *" + chat_title + "*\n\n"
            "👤 *Reported by:* " + driver_name + "\n"
            "📝 *Issue:* " + text[:200]
        )

        for admin in recipients:
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
                notified += 1
            except TelegramError as e:
                logger.warning(f"Could not DM admin {admin['id']}: {e}")

        self._persist()
        if notified == 0:
            logger.warning("No admins could be reached for alert!")

    # ── AI channel ────────────────────────────────────────────────────────────

    async def handle_channel_post(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = update.channel_post or update.effective_message
        if not msg or not msg.text or "AI DETECTED ISSUE" not in msg.text:
            return
        from config import config as _cfg
        channel_id = getattr(_cfg, "AI_ALERTS_CHANNEL_ID", 0)
        if channel_id and msg.chat.id != channel_id:
            return
        await self._process_ai_message(msg, ctx)

    async def _process_ai_message(self, message, ctx):
        import re as _re
        try:
            text       = message.text or ""
            uuid_match = _re.search(
                r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
                text, _re.IGNORECASE,
            )
            if not uuid_match:
                return
            alert_id = uuid_match.group(0)
            if alert_id in self._processed_ai_ids:
                return
            self._processed_ai_ids.add(alert_id)

            driver_name = "Unknown"
            group_name  = "Driver Group"
            summary     = ""
            confidence  = "HIGH"
            original    = ""

            for line in text.split("\n"):
                clean = line.strip().replace("*", "").replace("`", "")
                if clean.startswith("Driver:"):    driver_name = clean[7:].strip()
                elif clean.startswith("Group:"):   group_name  = clean[6:].strip()
                elif clean.startswith("Issue:"):   summary     = clean[6:].strip()
                elif clean.startswith("Confidence:"): confidence = clean[11:].strip()
                elif clean.startswith("Message:"): original    = clean[8:].strip().strip("_")

            now = datetime.now(timezone.utc)
            self._alerts[alert_id] = {
                "alert_id":          alert_id,
                "recipients":        {},
                "taken_by":          None,
                "created_at":        now.isoformat(),
                "last_escalated_at": None,
                "escalation_count":  0,
                "driver_id":         0,
                "driver_name":       driver_name,
                "driver_username":   None,
                "group_name":        group_name,
                "text":              original or summary,
                "source":            "ai_scanner",
            }

            short_id = self._register_alert(alert_id)
            kb       = self._make_kb(short_id)
            dm_text  = (
                "🤖 *AI Detected Issue* in *" + group_name + "*\n\n"
                "👤 *Driver:* " + driver_name + "\n"
                "📝 *Issue:* " + summary + "\n"
                "_" + confidence + " confidence_"
            )

            recipients = get_on_shift_admins() or get_all_admins()
            for admin in recipients:
                try:
                    sent = await ctx.bot.send_message(
                        admin["id"], dm_text,
                        parse_mode="Markdown", reply_markup=kb,
                    )
                    self._alerts[alert_id]["recipients"].setdefault(admin["id"], []).append(sent.message_id)
                except Exception as e:
                    logger.warning(f"Could not DM admin {admin['id']}: {e}")

            self._persist()
        except Exception as e:
            logger.error(f"AI channel error: {e}")

    # ── Assignment ────────────────────────────────────────────────────────────

    async def _do_assign(self, admin, name, alert_id, record, ctx):
        lock = self._get_lock(alert_id)
        async with lock:
            if record["taken_by"] is not None:
                # This is a reassignment — previous agent loses the case
                prev_agent_id = record["taken_by"][0] if record["taken_by"] else None
                record["taken_by"] = (admin.id, name)
                record["_prev_agent_id"] = prev_agent_id
            else:
                record["taken_by"] = (admin.id, name)
                record["_prev_agent_id"] = None

        prev_agent_id = record.pop("_prev_agent_id", None)

        for aid, mids in record["recipients"].items():
            for mid in mids:
                try:
                    if aid == admin.id:
                        await ctx.bot.delete_message(chat_id=aid, message_id=mid)
                    else:
                        await ctx.bot.edit_message_text(
                            chat_id=aid, message_id=mid,
                            text=f"✅ Case assigned to {_esc(name)}.\nNo action needed.",
                            reply_markup=None,
                        )
                except TelegramError:
                    pass

        # Reassign: update case owner — removes from old agent, appears for new agent
        assign_case(
            case_id=alert_id, agent_id=admin.id,
            agent_name=name, agent_username=admin.username,
        )
        self._persist()

        # Notify previous agent their case was taken over
        if prev_agent_id and prev_agent_id != admin.id:
            try:
                await ctx.bot.send_message(
                    prev_agent_id,
                    f"🔁 *Case taken over by {_esc(name)}*\n\n"
                    f"The case you reassigned has been accepted.\n"
                    f"It has been removed from your active cases.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError:
                pass

        from config import config as cfg
        from shifts import MAIN_ADMIN_ID
        dest_id = cfg.REPORTS_GROUP_ID or next(iter(MAIN_ADMIN_ID), None)
        if dest_id:
            created_at = record.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            secs = int((datetime.now(timezone.utc) - created_at).total_seconds()) if created_at else 0
            action = "Reassigned" if prev_agent_id else "Assigned"
            report_text = (
                f"✅ *Case {action}*\n\n"
                f"📌 *Group:* {record.get('group_name', '—')}\n"
                f"👤 *Reported by:* {record.get('driver_name', '—')}\n"
                f"🙋 *Handled by:* {_esc(name)}\n"
                f"⏱ *Response:* {secs}s\n"
                f"📝 {record.get('text', '(no details)')[:200]}"
            )
            try:
                sent = await ctx.bot.send_message(dest_id, report_text, parse_mode=ParseMode.MARKDOWN)
                set_report_msg_id(alert_id, sent.message_id)
            except TelegramError as e:
                logger.warning(f"Could not post assignment to reports: {e}")

        return True

    async def handle_assignment(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query  = update.callback_query
        await query.answer()
        admin  = update.effective_user

        if not is_authorized(admin.id):
            await query.answer("Not authorized.", show_alert=True)
            return

        name     = f"{admin.first_name} {admin.last_name or ''}".strip()
        parts    = query.data.split("|")
        action   = parts[0]
        short_id = parts[1] if len(parts) > 1 else ""

        alert_id, record = self._resolve(short_id)

        # If not in memory, try reloading from disk (happens after bot restart)
        if not record:
            self.load_from_disk()
            alert_id, record = self._resolve(short_id)

        if not record:
            # Truly gone — already assigned and cleaned up
            await query.edit_message_text(
                "✅ This alert was already handled.", reply_markup=None
            )
            return

        if action == "ignore":
            await query.edit_message_text(
                "🚫 You ignored this alert. Another agent can still take it.",
                reply_markup=None,
            )
            return

        if action in ("assign", "assignrpt"):
            # Block only if this exact agent already owns it
            if record["taken_by"] is not None and record["taken_by"][0] == admin.id:
                await query.edit_message_text(
                    "✅ You already have this case. Use /mycases to manage it.",
                    reply_markup=None,
                )
                return

            saved = dict(record)
            success = await self._do_assign(admin, name, alert_id, record, ctx)
            if not success:
                await query.edit_message_text(
                    "✅ Already assigned to someone else.", reply_markup=None
                )
                return

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            case_text = (
                f"📋 *Active Case*\n\n"
                f"📌 *Group:* {saved.get('group_name', '—')}\n"
                f"👤 *Reported by:* {saved.get('driver_name', '—')}\n"
                f"📝 *Issue:* {(saved.get('text') or '—')[:200]}"
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
            except TelegramError:
                pass

    async def handle_reassign(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        admin = update.effective_user
        name  = f"{admin.first_name} {admin.last_name or ''}".strip()

        # Find the alert_id from the case_id in the button that triggered this
        case_id   = query.data.replace("reassign_", "")
        alert_id  = case_id  # alert_id == case_id throughout this bot
        record    = self._alerts.get(alert_id)

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

        for a in get_all_admins():
            if a["id"] == admin.id:
                continue
            try:
                sent = await ctx.bot.send_message(
                    a["id"], dm_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=kb,
                )
                # Track so assignment can clean up these messages too
                if record is not None:
                    record["recipients"].setdefault(a["id"], []).append(sent.message_id)
            except TelegramError:
                pass

        if record is not None:
            self._persist()
