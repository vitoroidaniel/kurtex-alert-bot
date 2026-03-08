"""
handlers/alert_handler.py
"""
import asyncio
import logging
import uuid
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError
from shift_manager import get_on_shift_admins, get_all_admins
from storage import case_store

logger = logging.getLogger(__name__)

TRIGGER_WORDS = ['#maintenance', '#issue', '#breakdown', '#problem', '#help', '#emergency']

# Minimum seconds between alerts from the same driver (prevents spam)
COOLDOWN_SECONDS = 30


async def _delete_after(bot, chat_id, message_id, seconds):
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass


class AlertHandler:
    def __init__(self):
        self._alerts: dict[str, dict] = {}
        self._driver_last_time: dict[int, datetime] = {}  # driver_id → last alert time
        self._short_map: dict[str, str] = {}
        self._last_ai_update_id: int = -1
        self._processed_ai_ids: set = set()

    def _make_kb(self, short_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Assign", callback_data=f"assign|{short_id}"),
            InlineKeyboardButton("🚫 Ignore", callback_data=f"ignore|{short_id}"),
        ]])

    def _make_case_kb(self, short_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("📋 Report & Close", callback_data=f"assignrpt|{short_id}"),
            InlineKeyboardButton("✅ Close",           callback_data=f"close|{short_id}"),
        ]])

    def _register_alert(self, alert_id: str) -> str:
        short_id = alert_id.replace("-", "")[:12]
        self._short_map[short_id] = alert_id
        return short_id

    def _resolve(self, short_id: str):
        alert_id = self._short_map.get(short_id, short_id)
        return alert_id, self._alerts.get(alert_id)

    async def handle(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = update.effective_message
        if not msg or not update.effective_user or update.effective_user.is_bot:
            return

        text  = msg.text or msg.caption or ""
        photo = msg.photo[-1] if msg.photo else None

        logger.info(f"handle() called: chat={update.effective_chat.id if update.effective_chat else None} user={update.effective_user.id} text={repr(text[:60])}")

        matched = next((w for w in TRIGGER_WORDS if w.lower() in text.lower()), None)
        if not matched:
            logger.info(f"handle(): no trigger word found in text")
            return
        logger.info(f"handle(): matched trigger '{matched}'")

        user      = update.effective_user
        driver_id = user.id
        now       = datetime.now()

        # Simple cooldown — only one alert per driver per COOLDOWN_SECONDS
        last_time = self._driver_last_time.get(driver_id)
        if last_time and (now - last_time).total_seconds() < COOLDOWN_SECONDS:
            return

        self._driver_last_time[driver_id] = now

        chat_title  = update.effective_chat.title or "Driver Group"
        driver_name = f"{user.first_name} {user.last_name or ''}".strip()

        dm_text = (
            "\U0001f514 You have been mentioned in *" + chat_title + "*\n\n"
            "\U0001f464 *Driver:* " + driver_name + "\n"
            "\U0001f4dd *Issue:* " + text[:200]
        )

        # Create alert record and DB case
        alert_id = str(uuid.uuid4())
        self._new_alert(alert_id, driver_id, user, chat_title, text, now)
        case_store.create_case(
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
                logger.info(f"Alerted admin {admin['name']} ({admin['id']})")
            except TelegramError as e:
                logger.warning(f"Could not DM admin {admin['id']}: {e}")

        if notified == 0:
            logger.warning("No admins could be reached!")

    def _new_alert(self, alert_id, driver_id, user, chat_title, text, now):
        self._alerts[alert_id] = {
            "recipients":      {},
            "taken_by":        None,
            "created_at":      now,
            "driver_id":       driver_id,
            "driver_name":     f"{user.first_name} {user.last_name or ''}".strip(),
            "driver_username": user.username or None,
            "group_name":      chat_title,
            "text":            text,
        }

    # ── AI Channel ────────────────────────────────────────────────────────────

    async def handle_ai_channel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.channel_post or update.message
        if not msg or not msg.text:
            return
        if "AI DETECTED ISSUE" not in msg.text:
            return
        await self._process_ai_channel_message(msg, ctx)

    async def _process_ai_channel_message(self, message, ctx) -> None:
        import re as _re
        try:
            text = message.text or ""
            uuid_match = _re.search(
                r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
                text, _re.IGNORECASE
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
            original_text = ""

            for line in text.split("\n"):
                clean = line.strip().replace("*", "").replace("`", "")
                if clean.startswith("Driver:"):
                    driver_name = clean[len("Driver:"):].strip()
                elif clean.startswith("Group:"):
                    group_name = clean[len("Group:"):].strip()
                elif clean.startswith("Issue:"):
                    summary = clean[len("Issue:"):].strip()
                elif clean.startswith("Confidence:"):
                    confidence = clean[len("Confidence:"):].strip()
                elif clean.startswith("Message:"):
                    original_text = clean[len("Message:"):].strip().strip("_")

            now = datetime.now()
            self._alerts[alert_id] = {
                "recipients":      {},
                "taken_by":        None,
                "created_at":      now,
                "driver_id":       0,
                "driver_name":     driver_name,
                "driver_username": None,
                "group_name":      group_name,
                "text":            original_text or summary,
                "source":          "ai_scanner",
            }
            case_store.create_case(
                case_id=alert_id,
                driver_name=driver_name,
                driver_username=None,
                group_name=group_name,
                description=original_text or summary,
            )

            short_id = self._register_alert(alert_id)
            kb       = self._make_kb(short_id)

            dm_text = (
                "\U0001f916 *AI Detected Issue* in *" + group_name + "*\n\n"
                "\U0001f464 *Driver:* " + driver_name + "\n"
                "\U0001f4dd *Issue:* " + summary + "\n"
                "_Detected automatically \u2014 " + confidence + " confidence_"
            )

            recipients = get_on_shift_admins() or get_all_admins()
            notified   = 0
            for admin in recipients:
                try:
                    sent = await ctx.bot.send_message(
                        admin["id"], dm_text,
                        parse_mode="Markdown", reply_markup=kb,
                    )
                    self._alerts[alert_id]["recipients"].setdefault(admin["id"], []).append(sent.message_id)
                    notified += 1
                except Exception as e:
                    logger.warning(f"Could not DM admin {admin['id']}: {e}")

            logger.info(f"AI alert {alert_id} forwarded to {notified} admins")
        except Exception as e:
            logger.error(f"Error processing AI channel message: {e}")

    async def poll_ai_alerts(self, ctx) -> None:
        try:
            from config import config as main_config
            channel_id = getattr(main_config, "AI_ALERTS_CHANNEL_ID", 0)
            if not channel_id:
                return
            updates = await ctx.bot.get_updates(
                offset=self._last_ai_update_id + 1 if self._last_ai_update_id >= 0 else None,
                limit=20, timeout=0,
                allowed_updates=["channel_post"],
            )
            for upd in updates:
                self._last_ai_update_id = upd.update_id
                msg = upd.channel_post
                if not msg or msg.chat.id != channel_id:
                    continue
                if not msg.text or "AI DETECTED ISSUE" not in msg.text:
                    continue
                await self._process_ai_channel_message(msg, ctx)
        except Exception as e:
            logger.error(f"poll_ai_alerts error: {e}")

    async def handle_channel_post(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = update.channel_post or update.effective_message
        if not msg or not msg.text:
            return
        if "AI DETECTED ISSUE" not in msg.text:
            return
        from config import config as main_config
        channel_id = getattr(main_config, "AI_ALERTS_CHANNEL_ID", 0)
        if channel_id and msg.chat.id != channel_id:
            return
        await self._process_ai_channel_message(msg, ctx)

    # ── Assignment ────────────────────────────────────────────────────────────

    async def _do_assign(self, admin, tag, name, alert_id, record, ctx):
        if record["taken_by"] is not None:
            return False

        record["taken_by"] = (admin.id, tag)

        # Remove alert buttons from all other admins
        for aid, mids in record["recipients"].items():
            for mid in mids:
                try:
                    if aid == admin.id:
                        await ctx.bot.delete_message(chat_id=aid, message_id=mid)
                    else:
                        await ctx.bot.edit_message_text(
                            chat_id=aid, message_id=mid,
                            text=f"✅ Case assigned to {tag}.\nNo action needed.",
                            reply_markup=None
                        )
                except TelegramError:
                    pass

        case_store.assign_case(
            case_id=alert_id, agent_id=admin.id,
            agent_name=name, agent_username=admin.username,
        )

        # Post to reports group
        from config import config as cfg
        from shift_manager import MAIN_ADMIN_ID
        dest_id = cfg.REPORTS_GROUP_ID or MAIN_ADMIN_ID
        if dest_id:
            created_at = record.get("created_at", datetime.now())
            secs = int((datetime.now() - created_at).total_seconds())
            report = (
                f"✅ *Case Assigned*\n\n"
                f"📌 *Group:* {record.get('group_name', '—')}\n"
                f"👤 *Driver:* {record.get('driver_name', '—')}\n"
                f"🙋 *Handler:* {name}\n"
                f"⏱ *Response:* {secs // 60}m {secs % 60}s\n"
                f"📝 {record.get('text', '(no details)')}"
            )
            try:
                await ctx.bot.send_message(dest_id, report, parse_mode=ParseMode.MARKDOWN)
            except TelegramError as e:
                logger.warning(f"Could not send report: {e}")

        # Keep alert in self._alerts so mycases can still find it via case_store
        # Just mark it taken — don't pop it
        return True

    async def handle_assignment(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query    = update.callback_query
        await query.answer()
        admin    = update.effective_user
        name     = f"{admin.first_name} {admin.last_name or ''}".strip()
        tag      = f"@{admin.username}" if admin.username else name
        parts    = query.data.split("|")
        action   = parts[0]
        short_id = parts[1] if len(parts) > 1 else ""

        alert_id, record = self._resolve(short_id)

        if not record:
            await query.edit_message_text("⚠️ Alert expired.", reply_markup=None)
            return

        if action == "ignore":
            await query.edit_message_text(
                "🚫 You ignored this alert. Another agent can still take it.",
                reply_markup=None
            )
            return

        if action in ("assign", "assignrpt"):
            if record["taken_by"] is not None:
                already = record["taken_by"][1]
                await query.edit_message_text(
                    f"✅ Already assigned to {already}.\nNo action needed.",
                    reply_markup=None
                )
                return

            saved_record = dict(record)
            success = await self._do_assign(admin, tag, name, alert_id, record, ctx)

            if not success:
                await query.edit_message_text(
                    "✅ Already assigned to someone else.\nNo action needed.",
                    reply_markup=None
                )
                return

            if action == "assign":
                try:
                    await ctx.bot.send_message(
                        admin.id,
                        "✅ Case assigned to you\\. Use /mycases to view your cases\\.",
                        parse_mode="MarkdownV2",
                    )
                except TelegramError:
                    pass

            elif action == "assignrpt":
                ctx.user_data["report"] = {
                    "handler":      tag,
                    "alert_record": saved_record,
                    "photos":       [],
                    "driver_name":  saved_record.get("driver_name", ""),
                    "group_name":   saved_record.get("group_name", ""),
                    "issue":        saved_record.get("text", ""),
                }
                await ctx.bot.send_message(
                    admin.id,
                    "📋 *New Case Report*\n\nIs this a Truck or Trailer issue?",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🚛 Truck",   callback_data="rpt_type|truck"),
                        InlineKeyboardButton("🚜 Trailer", callback_data="rpt_type|trailer"),
                        InlineKeyboardButton("❄️ Reefer",  callback_data="rpt_type|reefer"),
                    ]])
                )

    async def handle_reassign(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        admin = update.effective_user
        name  = f"{admin.first_name} {admin.last_name or ''}".strip()

        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"🔁 *{name}* marked this for reassignment. Escalating to all admins...",
            parse_mode=ParseMode.MARKDOWN
        )

        all_admins = get_all_admins()
        original   = query.message.caption or query.message.text or ""
        for a in all_admins:
            if a["id"] == admin.id:
                continue
            try:
                await ctx.bot.send_message(
                    a["id"],
                    f"🔁 *Escalation* — {name} needs someone to take over:\n\n{original}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except TelegramError:
                pass
