"""handlers/alert_handler.py -- Fast, SQLite-backed alert routing and assignment."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import threading
import uuid
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from backend.core.app_time import parse_timestamp, utc_now
from backend.core.shift_manager import get_all_admins, get_on_shift_admins
from backend.storage.case_store import (
    async_assign_case,
    async_create_case,
    async_delete_active_alert,
    async_get_case,
    async_set_report_msg_id,
    async_upsert_active_alert,
    load_active_alerts,
)
from backend.storage.user_store import is_authorized
from bot_app.telegram_utils import (
    concurrent_ack,
    esc_html,
    safe_answer,
    safe_delete_message,
    safe_edit_message_text,
    safe_send_message,
)

logger = logging.getLogger(__name__)
TRIGGER_WORDS = ["#maintenance", "#repairs", "#repair"]

COOLDOWN_MIN_SECONDS = 5 * 60
COOLDOWN_MAX_SECONDS = 6 * 60
UNASSIGNED_NUDGE_COOLDOWN_SECONDS = 2 * 60

_HTML_MODE = getattr(ParseMode, "HTML", "HTML")


class AlertHandler:
    def __init__(self):
        self._alerts: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._driver_last_time: dict[int, datetime] = {}
        self._driver_cooldown_until: dict[int, datetime] = {}
        self._driver_last_alert: dict[int, str] = {}
        self._last_nudge_at: dict[str, datetime] = {}
        self._short_map: dict[str, str] = {}
        self._state_lock = threading.RLock()
        self._reload_lock = asyncio.Lock()
        self._bg_tasks: set[asyncio.Task] = set()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load_from_disk(self):
        """Reload open/active alerts in one SQLite query (excludes closed cases)."""
        raw = load_active_alerts()
        with self._state_lock:
            self._alerts.clear()
            self._short_map.clear()
            for aid, record in raw.items():
                self._alerts[aid] = record
                self._short_map[aid.replace("-", "")[:12]] = aid
        logger.info("Loaded %d active alerts from database", len(raw))

    def _evict_memory(self, alert_id: str) -> None:
        with self._state_lock:
            self._alerts.pop(alert_id, None)
            self._locks.pop(alert_id, None)
            self._last_nudge_at.pop(alert_id, None)
            self._short_map.pop(alert_id.replace("-", "")[:12], None)

    def _spawn_bg(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)

        def _done(t: asyncio.Task):
            self._bg_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error(
                    "Background assignment update failed: %s",
                    exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_done)
        return task

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_kb(self, alert_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Assign", callback_data=f"assign|{alert_id}"),
            InlineKeyboardButton("🚫 Ignore", callback_data=f"ignore|{alert_id}"),
        ]])

    def _register_alert(self, alert_id: str) -> str:
        short_id = alert_id.replace("-", "")[:12]
        with self._state_lock:
            self._short_map[short_id] = alert_id
        return alert_id

    def _resolve(self, token: str):
        with self._state_lock:
            if token in self._alerts:
                return token, self._alerts[token]
            alert_id = self._short_map.get(token, token)
            return alert_id, self._alerts.get(alert_id)

    async def _restore_from_case(self, case_id: str):
        """Rebuild callback state when alert was evicted or restarted."""
        case = await async_get_case(case_id)
        if not case or case.get("status") == "done":
            return case_id, None
        record = {
            "alert_id": case_id,
            "recipients": {},
            "taken_by": (
                [case.get("agent_id"), case.get("agent_name")]
                if case.get("agent_id") is not None
                else None
            ),
            "created_at": case.get("opened_at") or utc_now().isoformat(),
            "last_escalated_at": None,
            "escalation_count": 0,
            "driver_id": 0,
            "driver_name": case.get("driver_name") or "Unknown",
            "driver_username": case.get("driver_username"),
            "group_name": case.get("group_name") or "Driver Group",
            "text": case.get("description") or "",
        }
        with self._state_lock:
            self._alerts[case_id] = record
            self._short_map[case_id.replace("-", "")[:12]] = case_id
        if record["taken_by"] is None:
            await async_upsert_active_alert(case_id, record)
        logger.info("Restored alert state for case %s", case_id)
        return case_id, record

    def _get_lock(self, alert_id: str) -> asyncio.Lock:
        with self._state_lock:
            if alert_id not in self._locks:
                self._locks[alert_id] = asyncio.Lock()
            return self._locks[alert_id]

    # ── Group trigger handler ─────────────────────────────────────────────────

    async def handle(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = update.effective_message
        if not msg:
            return

        is_anonymous = msg.sender_chat is not None
        if not is_anonymous and (not update.effective_user or update.effective_user.is_bot):
            return

        text = msg.text or msg.caption or ""
        photo = msg.photo[-1] if msg.photo else None

        def _match(word: str, hay: str) -> bool:
            if word.startswith("#"):
                return word.lower() in hay.lower()
            return bool(re.search(r"\b" + re.escape(word) + r"\b", hay, re.IGNORECASE))

        if not any(_match(w, text) for w in TRIGGER_WORDS):
            return

        driver_id = msg.sender_chat.id if is_anonymous else update.effective_user.id
        now = utc_now()

        cooldown_until = self._driver_cooldown_until.get(driver_id)
        if cooldown_until:
            cooldown_until = parse_timestamp(cooldown_until)
            if not cooldown_until:
                self._driver_cooldown_until.pop(driver_id, None)
            elif now < cooldown_until:
                await self._nudge_if_unassigned(driver_id, ctx)
                return

        self._driver_last_time[driver_id] = now
        self._driver_cooldown_until[driver_id] = now + timedelta(
            seconds=random.uniform(COOLDOWN_MIN_SECONDS, COOLDOWN_MAX_SECONDS)
        )

        chat_title = update.effective_chat.title or "Driver Group"
        if is_anonymous:
            driver_name = msg.sender_chat.title or chat_title
            driver_username = None
        else:
            user = update.effective_user
            driver_name = f"{user.first_name} {user.last_name or ''}".strip()
            driver_username = user.username or None
        alert_id = str(uuid.uuid4())

        record = {
            "alert_id":          alert_id,
            "recipients":        {},
            "taken_by":          None,
            "created_at":        now.isoformat(),
            "last_escalated_at": None,
            "escalation_count":  0,
            "driver_id":         driver_id,
            "driver_name":       driver_name,
            "driver_username":   driver_username,
            "group_name":        chat_title,
            "text":              text,
        }
        with self._state_lock:
            self._alerts[alert_id] = record
            self._short_map[alert_id.replace("-", "")[:12]] = alert_id

        await async_create_case(
            case_id=alert_id,
            driver_name=driver_name,
            driver_username=driver_username,
            group_name=chat_title,
            description=text,
        )
        self._driver_last_alert[driver_id] = alert_id

        kb = self._make_kb(self._register_alert(alert_id))
        recipients = await asyncio.to_thread(lambda: get_on_shift_admins() or get_all_admins())
        dm_text = (
            f"🔔 You have been mentioned in <b>{esc_html(chat_title)}</b>\n\n"
            f"👤 <b>Reported by:</b> {esc_html(driver_name)}\n"
            f"📝 <b>Issue:</b> {esc_html(text[:200])}"
        )

        async def notify_admin(admin):
            try:
                if photo:
                    sent = await ctx.bot.send_photo(
                        admin["id"],
                        photo=photo.file_id,
                        caption=dm_text,
                        parse_mode=_HTML_MODE,
                        reply_markup=kb,
                    )
                else:
                    sent = await safe_send_message(
                        ctx.bot,
                        admin["id"],
                        dm_text,
                        reply_markup=kb,
                    )
                if sent:
                    record["recipients"].setdefault(admin["id"], []).append(sent.message_id)
                return bool(sent)
            except TelegramError as exc:
                logger.warning("Could not DM admin %s: %s", admin["id"], exc)
                return False

        notified = 0
        if recipients:
            results = await asyncio.gather(*(notify_admin(a) for a in recipients), return_exceptions=True)
            notified = sum(result is True for result in results)

        await async_upsert_active_alert(alert_id, record)
        if notified == 0:
            logger.warning("No admins could be reached for alert!")

    async def _nudge_if_unassigned(self, driver_id: int, ctx: ContextTypes.DEFAULT_TYPE):
        alert_id = self._driver_last_alert.get(driver_id)
        if not alert_id:
            return
        with self._state_lock:
            record = self._alerts.get(alert_id)
        if not record or record.get("taken_by") is not None:
            return

        now = utc_now()
        last_nudge = self._last_nudge_at.get(alert_id)
        if last_nudge and (now - last_nudge).total_seconds() < UNASSIGNED_NUDGE_COOLDOWN_SECONDS:
            return
        self._last_nudge_at[alert_id] = now

        kb = self._make_kb(self._register_alert(alert_id))
        text = (
            "⚠️ <b>Case still unassigned!</b>\n\n"
            f"👤 <b>Driver:</b> {esc_html(record.get('driver_name', '—'))} keeps reporting "
            "this issue and no one has picked it up yet.\n"
            f"📌 <b>Group:</b> {esc_html(record.get('group_name', '—'))}\n"
            f"📝 <b>Issue:</b> {esc_html((record.get('text') or '—')[:200])}\n\n"
            "Please assign it."
        )

        recipients = await asyncio.to_thread(lambda: get_on_shift_admins() or get_all_admins())

        async def send_nudge(admin):
            try:
                sent = await safe_send_message(ctx.bot, admin["id"], text, reply_markup=kb)
                if sent:
                    record["recipients"].setdefault(admin["id"], []).append(sent.message_id)
            except TelegramError as exc:
                logger.warning("Could not nudge admin %s: %s", admin["id"], exc)

        if recipients:
            await asyncio.gather(*(send_nudge(a) for a in recipients), return_exceptions=True)

        await async_upsert_active_alert(alert_id, record)

    # ── Assignment ────────────────────────────────────────────────────────────

    async def _replace_alert_message(self, query, text: str):
        try:
            if query.message and query.message.caption is not None:
                await query.edit_message_caption(caption=text, reply_markup=None)
            else:
                await safe_edit_message_text(query, text, parse_mode=None, reply_markup=None)
        except TelegramError as exc:
            logger.warning("Could not update alert message: %s", exc)

    async def _cleanup_assignment_messages(self, record: dict, admin_id: int, name: str, ctx):
        done_text = f"✅ Case assigned to {name}.\nNo action needed."
        sem = asyncio.Semaphore(8)

        async def clean_one(chat_id, message_id):
            async with sem:
                try:
                    cid = int(chat_id)
                    if cid == admin_id:
                        await safe_delete_message(ctx.bot, cid, message_id)
                        return
                    try:
                        await ctx.bot.edit_message_text(
                            chat_id=cid,
                            message_id=message_id,
                            text=done_text,
                            reply_markup=None,
                        )
                    except TelegramError as exc:
                        err = str(exc).lower()
                        if "there is no text" in err or "caption" in err:
                            await ctx.bot.edit_message_caption(
                                chat_id=cid,
                                message_id=message_id,
                                caption=done_text,
                                reply_markup=None,
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
        try:
            await self._cleanup_assignment_messages(record, admin.id, name, ctx)

            if prev_agent_id and prev_agent_id != admin.id:
                await safe_send_message(
                    ctx.bot,
                    prev_agent_id,
                    f"🔁 <b>Case taken over by {esc_html(name)}</b>\n\n"
                    "The case you reassigned has been accepted.\n"
                    "It has been removed from your active cases.",
                )

            from backend.core.config import config as cfg
            dest_id = cfg.REPORTS_GROUP_ID
            if dest_id:
                created_at = parse_timestamp(record.get("created_at"))
                secs = int((utc_now() - created_at).total_seconds()) if created_at else 0
                action = "Reassigned" if prev_agent_id else "Assigned"
                report_text = (
                    f"✅ <b>Case {action}</b>\n\n"
                    f"📌 <b>Group:</b> {esc_html(record.get('group_name', '—'))}\n"
                    f"👤 <b>Reported by:</b> {esc_html(record.get('driver_name', '—'))}\n"
                    f"🙋 <b>Handled by:</b> {esc_html(name)}\n"
                    f"⏱ <b>Response:</b> {secs}s\n"
                    f"📝 {esc_html((record.get('text') or '(no details)')[:200])}"
                )
                try:
                    sent = await safe_send_message(ctx.bot, dest_id, report_text)
                    if sent:
                        await async_set_report_msg_id(alert_id, sent.message_id)
                except TelegramError as exc:
                    logger.warning("Could not post assignment to reports for %s: %s", alert_id, exc)
        finally:
            # Once claimed & old DMs cleaned, evict from active_alerts table & memory.
            with self._state_lock:
                current = self._alerts.get(alert_id)
                still_assigned = current is None or (
                    current.get("taken_by") is not None and not current.get("reassign_requested")
                )
            if still_assigned:
                await async_delete_active_alert(alert_id)
                self._evict_memory(alert_id)

    async def _do_assign(self, admin, name, alert_id, record, ctx):
        lock = self._get_lock(alert_id)
        async with lock:
            previous = record.get("taken_by")
            prev_agent_id = previous[0] if previous else None
            allow_reassign = bool(record.get("reassign_requested"))
            claimed = await async_assign_case(
                case_id=alert_id,
                agent_id=admin.id,
                agent_name=name,
                agent_username=admin.username,
                allow_reassign=allow_reassign,
            )
            if not claimed:
                return False
            record["taken_by"] = [admin.id, name]
            record.pop("reassign_requested", None)
            snapshot = {
                **record,
                "recipients": {k: list(v) for k, v in (record.get("recipients") or {}).items()},
            }
            await async_upsert_active_alert(alert_id, snapshot)

        self._spawn_bg(
            self._post_assignment_updates(snapshot, admin, name, alert_id, prev_agent_id, ctx)
        )
        return True

    async def handle_assignment(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        admin = update.effective_user
        if query is None or admin is None:
            return

        if not is_authorized(admin.id):
            await safe_answer(query, "Not authorized.", show_alert=True)
            return

        async with concurrent_ack(query, "Processing..."):
            name = f"{admin.first_name} {admin.last_name or ''}".strip()
            parts = (query.data or "").split("|")
            action = parts[0]
            short_id = parts[1] if len(parts) > 1 else ""

            alert_id, record = self._resolve(short_id)

            if not record:
                async with self._reload_lock:
                    alert_id, record = self._resolve(short_id)
                    if not record:
                        await asyncio.to_thread(self.load_from_disk)
                        alert_id, record = self._resolve(short_id)

            if not record:
                alert_id, record = await self._restore_from_case(alert_id)

            if not record:
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

                case_text = (
                    f"📋 <b>Active Case</b>\n\n"
                    f"📌 <b>Group:</b> {esc_html(saved.get('group_name', '—'))}\n"
                    f"👤 <b>Reported by:</b> {esc_html(saved.get('driver_name', '—'))}\n"
                    f"📝 <b>Issue:</b> {esc_html((saved.get('text') or '—')[:200])}"
                )
                case_kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Solve",    callback_data=f"close_ask|{alert_id}"),
                    InlineKeyboardButton("📋 Report",   callback_data=f"solve|{alert_id}"),
                    InlineKeyboardButton("🔁 Reassign", callback_data=f"reassign_{alert_id}"),
                ]])
                try:
                    await safe_send_message(ctx.bot, admin.id, case_text, reply_markup=case_kb)
                except TelegramError as exc:
                    logger.error("Case %s was assigned but active-case DM failed: %s", alert_id, exc)

    async def handle_reassign(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        admin = update.effective_user
        async with concurrent_ack(query):
            name = f"{admin.first_name} {admin.last_name or ''}".strip()

            alert_id = query.data.replace("reassign_", "", 1)
            with self._state_lock:
                record = self._alerts.get(alert_id)
            if record is None:
                _, record = await self._restore_from_case(alert_id)

            if record is None:
                await self._replace_alert_message(query, "This case is no longer active.")
                return

            record["reassign_requested"] = True
            await async_upsert_active_alert(alert_id, record)

            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except TelegramError:
                pass
            await safe_send_message(
                ctx.bot,
                admin.id,
                f"🔁 <b>{esc_html(name)}</b> marked this for reassignment. Notifying other agents...",
            )

            original = (query.message.caption or query.message.text or "") if query.message else ""
            dm_text = (
                f"🔁 <b>Reassign Request</b> — {esc_html(name)} needs someone to take over:\n\n"
                f"{esc_html(original[:300])}"
            )

            kb = self._make_kb(self._register_alert(alert_id))

            async def notify_agent(a):
                try:
                    sent = await safe_send_message(ctx.bot, a["id"], dm_text, reply_markup=kb)
                    if sent:
                        record["recipients"].setdefault(a["id"], []).append(sent.message_id)
                except TelegramError as exc:
                    logger.warning("Could not send reassignment %s to %s: %s", alert_id, a["id"], exc)

            all_admins = await asyncio.to_thread(get_all_admins)
            recipients = [a for a in all_admins if a["id"] != admin.id]
            if recipients:
                await asyncio.gather(*(notify_agent(a) for a in recipients), return_exceptions=True)
            await async_upsert_active_alert(alert_id, record)
            logger.info("Reassignment requested for %s by %s", alert_id, admin.id)
