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
from shift_manager import get_on_shift_admins, get_all_admins, get_current_shift_name
from storage import case_store

logger = logging.getLogger(__name__)

TRIGGER_WORDS = ['#maintenance', '#issue', '#breakdown', '#problem', '#help', '#emergency']


async def _delete_after(bot, chat_id, message_id, seconds):
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass


class AlertHandler:
    def __init__(self):
        self._alerts: dict[str, dict] = {}
        self._driver_requests: dict[int, dict] = {}
        self._short_map: dict[str, str] = {}   # short_id -> full alert_id
        self.COOLDOWN_SECONDS = 5
        self.REMINDER_THRESHOLD = 3

    def _make_kb(self, short_id: str) -> InlineKeyboardMarkup:
        """Build alert keyboard — short_id keeps callback_data under 64 bytes."""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Assign",          callback_data=f"assign|{short_id}"),
                InlineKeyboardButton("📋 Assign & Report", callback_data=f"assignrpt|{short_id}"),
            ],
            [
                InlineKeyboardButton("🚫 Ignore",          callback_data=f"ignore|{short_id}"),
            ],
        ])

    def _register_alert(self, alert_id: str) -> str:
        """Create a short 12-char key for the callback and store the mapping."""
        short_id = alert_id.replace("-", "")[:12]
        self._short_map[short_id] = alert_id
        return short_id

    def _resolve(self, short_id: str):
        """Resolve short_id back to full alert_id and record."""
        alert_id = self._short_map.get(short_id, short_id)
        return alert_id, self._alerts.get(alert_id)

    async def handle(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = update.effective_message
        if not msg or not update.effective_user or update.effective_user.is_bot:
            return

        text  = msg.text or msg.caption or ""
        photo = msg.photo[-1] if msg.photo else None

        matched = next((w for w in TRIGGER_WORDS if w.lower() in text.lower()), None)
        if not matched:
            return

        user      = update.effective_user
        driver_id = user.id
        on_shift  = get_on_shift_admins()
        now       = datetime.now()

        if driver_id not in self._driver_requests:
            self._driver_requests[driver_id] = {
                "attempt_count": 0, "last_alert_id": None, "last_alert_time": None,
            }

        driver_rec    = self._driver_requests[driver_id]
        last_time     = driver_rec.get("last_alert_time")
        last_alert_id = driver_rec.get("last_alert_id")
        attempt_count = driver_rec.get("attempt_count", 0)

        is_reminder = False
        if attempt_count >= self.REMINDER_THRESHOLD - 1:
            if last_alert_id and last_alert_id in self._alerts:
                if not self._alerts[last_alert_id].get("taken_by"):
                    is_reminder = True
                else:
                    driver_rec["attempt_count"] = 0

        if not is_reminder:
            if last_time and (now - last_time).total_seconds() < self.COOLDOWN_SECONDS:
                return

        driver_rec["attempt_count"] += 1
        driver_rec["last_alert_time"] = now

        chat_title = update.effective_chat.title or "the driver group"
        dm_text = (
            f"⏰ *REMINDER:* Driver still needs help in *{chat_title}*!"
            if is_reminder
            else f"Hey, you've been mentioned in *{chat_title}*."
        )

        # Reuse or create alert
        if last_alert_id and last_alert_id in self._alerts:
            record = self._alerts[last_alert_id]
            if not record.get("taken_by"):
                for aid, mids in record["recipients"].items():
                    for mid in mids:
                        try:
                            await ctx.bot.delete_message(chat_id=aid, message_id=mid)
                        except TelegramError:
                            pass
                record["recipients"] = {}
                record["text"] = text
                alert_id = last_alert_id
            else:
                alert_id = str(uuid.uuid4())
                self._new_alert(alert_id, driver_id, user, chat_title, text, now)
                case_store.create_case(
                    case_id=alert_id,
                    driver_name=self._alerts[alert_id]["driver_name"],
                    driver_username=self._alerts[alert_id]["driver_username"],
                    group_name=chat_title, description=text,
                )
        else:
            alert_id = str(uuid.uuid4())
            self._new_alert(alert_id, driver_id, user, chat_title, text, now)
            case_store.create_case(
                case_id=alert_id,
                driver_name=self._alerts[alert_id]["driver_name"],
                driver_username=self._alerts[alert_id]["driver_username"],
                group_name=chat_title, description=text,
            )

        driver_rec["last_alert_id"] = alert_id
        short_id = self._register_alert(alert_id)
        kb = self._make_kb(short_id)

        recipients = on_shift if on_shift else get_all_admins()
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
            logger.warning("No admins could be reached! Check shifts.py and make sure admins have started the bot.")

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


    async def poll_ai_alerts(self, ctx) -> None:
        """Called by scheduler every 10s — picks up AI alerts from the AI Alerts channel."""
        try:
            from config import config as main_config
            channel_id = main_config.AI_ALERTS_CHANNEL_ID
            
            if not channel_id:
                return

            # Get messages from the AI Alerts channel
            # We need to track the last processed message ID
            last_id = getattr(self, '_last_ai_channel_message_id', 0)
            
            try:
                updates = await ctx.bot.get_updates(offset=last_id + 1, limit=10)
            except Exception as e:
                logger.debug(f"get_updates error: {e}")
                return

            for update in updates:
                if not update.channel_post:
                    continue
                if update.channel_post.chat.id != channel_id:
                    continue
                if not update.channel_post.text:
                    continue
                    
                msg_id = update.channel_post.message_id
                if msg_id <= last_id:
                    continue
                    
                last_id = max(last_id, msg_id)
                
                # Process the AI alert message
                await self._process_ai_channel_message(update.channel_post, ctx)
            
            self._last_ai_channel_message_id = last_id

        except Exception as e:
            logger.error(f"poll_ai_alerts error: {e}")

    async def _process_ai_channel_message(self, message, ctx) -> None:
        """Process an AI alert message from the AI Alerts channel."""
        try:
            text = message.text or ""
            
            # Check if this is an AI detected issue message
            if "🤖 *AI DETECTED ISSUE*" not in text:
                return
                
            # Extract alert_id from the message (it's in backticks at the end)
            alert_id = None
            for line in text.split('\n'):
                if line.strip().startswith('`') and line.strip().endswith('`'):
                    alert_id = line.strip().strip('`')
                    break
            
            if not alert_id:
                logger.warning("No alert_id found in AI channel message")
                return
            
            # Extract other details from the message
            driver_name = "Unknown"
            group_name = "Driver Group"
            summary = ""
            confidence = "HIGH"
            original_text = ""
            
            for line in text.split('\n'):
                if line.startswith("*Driver:*"):
                    driver_name = line.replace("*Driver:*", "").strip()
                elif line.startswith("*Group:*"):
                    group_name = line.replace("*Group:*", "").strip()
                elif line.startswith("*Issue:*"):
                    summary = line.replace("*Issue:*", "").strip()
                elif line.startswith("*Confidence:*"):
                    confidence = line.replace("*Confidence:*", "").strip()
                elif line.startswith("*Message:*"):
                    original_text = line.replace("*Message:*", "").strip().strip('_')
            
            # Create alert record
            now = datetime.now()
            self._alerts[alert_id] = {
                "recipients":      {},
                "taken_by":        None,
                "created_at":      now,
                "driver_id":       0,
                "driver_name":     driver_name,
                "driver_username": None,
                "group_name":      group_name,
                "text":            original_text,
                "source":          "ai_scanner",
            }

            # Create case
            from storage import case_store
            case_store.create_case(
                case_id=alert_id,
                driver_name=driver_name,
                driver_username=None,
                group_name=group_name,
                description=original_text,
            )

            short_id = self._register_alert(alert_id)
            kb = self._make_kb(short_id)

            dm_text = (
                f"\U0001f916 *AI Detected Issue*\n\n"
                f"\U0001f4cc *Group:* {group_name}\n"
                f"\U0001f464 *Driver:* {driver_name}\n"
                f"\u26a0\ufe0f *Issue:* {summary}\n"
                f"\U0001f4ac *Said:* _{original_text[:150]}_\n\n"
                f"_No trigger word \u2014 detected automatically ({confidence} confidence)_"
            )

            from shift_manager import get_on_shift_admins, get_all_admins
            recipients = get_on_shift_admins() or get_all_admins()
            notified = 0

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

            logger.info(f"AI alert {alert_id} sent to {notified} admins from channel")

        except Exception as e:
            logger.error(f"Error processing AI channel message: {e}")

    async def _process_ai_alert(self, alert: dict, ctx) -> None:
        """Turn an AI-detected alert into a full main bot alert with Assign & Report buttons."""
        import json as _json
        alert_id    = alert["id"]
        driver_name = alert.get("driver_name", "Unknown")
        group_name  = alert.get("group_name", "Driver Group")
        summary     = alert.get("summary", "")
        text        = alert.get("text", "")
        driver_id   = alert.get("driver_id", 0)
        confidence  = alert.get("confidence", "HIGH")
        now         = datetime.now()

        self._alerts[alert_id] = {
            "recipients":      {},
            "taken_by":        None,
            "created_at":      now,
            "driver_id":       driver_id,
            "driver_name":     driver_name,
            "driver_username": alert.get("driver_username"),
            "group_name":      group_name,
            "text":            text,
            "source":          "ai_scanner",
        }

        from storage import case_store
        case_store.create_case(
            case_id=alert_id,
            driver_name=driver_name,
            driver_username=alert.get("driver_username"),
            group_name=group_name,
            description=text,
        )

        short_id = self._register_alert(alert_id)
        kb       = self._make_kb(short_id)

        dm_text = (
            f"\U0001f916 *AI Detected Issue*\n\n"
            f"\U0001f4cc *Group:* {group_name}\n"
            f"\U0001f464 *Driver:* {driver_name}\n"
            f"\u26a0\ufe0f *Issue:* {summary}\n"
            f"\U0001f4ac *Said:* _{text[:150]}_\n\n"
            f"_No trigger word \u2014 detected automatically ({confidence} confidence)_"
        )

        from shift_manager import get_on_shift_admins, get_all_admins
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

        logger.info(f"AI alert {alert_id} sent to {notified} admins")

    async def _do_assign(self, admin, tag, name, alert_id, record, ctx):
        """Shared assign logic. Returns True on success, False if already taken."""
        if record["taken_by"] is not None:
            return False

        record["taken_by"] = (admin.id, tag)

        # Delete original alert message for this admin
        for mid in record["recipients"].get(admin.id, []):
            try:
                await ctx.bot.delete_message(chat_id=admin.id, message_id=mid)
            except TelegramError:
                pass

        # Update all other admins
        for aid, mids in record["recipients"].items():
            if aid == admin.id:
                continue
            for mid in mids:
                try:
                    await ctx.bot.edit_message_text(
                        chat_id=aid, message_id=mid,
                        text=f"✅ Case assigned to {tag}.\nNo action needed.",
                        reply_markup=None
                    )
                except TelegramError:
                    pass

        # Save to case store
        case_store.assign_case(
            case_id=alert_id, agent_id=admin.id,
            agent_name=name, agent_username=admin.username,
        )

        # Quick report to reports group
        from shift_manager import MAIN_ADMIN_ID
        from config import config as cfg
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

        driver_id = record.get("driver_id", 0)
        self._alerts.pop(alert_id, None)
        if driver_id in self._driver_requests:
            self._driver_requests[driver_id]["attempt_count"] = 0

        return True

    async def handle_assignment(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        admin = update.effective_user
        name  = f"{admin.first_name} {admin.last_name or ''}".strip()
        tag   = f"@{admin.username}" if admin.username else name

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

            # Store record details before _do_assign pops it
            saved_record = dict(record)
            success = await self._do_assign(admin, tag, name, alert_id, record, ctx)

            if not success:
                await query.edit_message_text(
                    f"✅ Already assigned to someone else.\nNo action needed.",
                    reply_markup=None
                )
                return

            if action == "assign":
                try:
                    sent = await ctx.bot.send_message(
                        admin.id, "✅ You are now handling this alert. Thanks!"
                    )
                    asyncio.create_task(_delete_after(ctx.bot, admin.id, sent.message_id, 5))
                except TelegramError:
                    pass

            elif action == "assignrpt":
                # Store alert info in user_data so report_handler can pre-fill it
                ctx.user_data["report"] = {
                    "handler":      tag,
                    "alert_record": saved_record,
                    "photos":       [],
                    "driver_name":  saved_record.get("driver_name", ""),
                    "group_name":   saved_record.get("group_name", ""),
                    "issue":        saved_record.get("text", ""),
                }

                # Send the first report question directly
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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