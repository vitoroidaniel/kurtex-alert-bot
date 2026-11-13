"""handlers/feedback_handler.py -- User bug/help reporting (/bug, /support) and manager/dev /alert broadcast."""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from backend.storage.feedback_store import async_create_feedback
from backend.storage.user_store import get_all_user_dicts, get_user, has_role
from bot_app.telegram_utils import (
    concurrent_ack,
    esc_html,
    safe_edit_message_text,
    safe_reply_text,
    safe_send_message,
)

logger = logging.getLogger(__name__)

ASK_FB_CATEGORY, ASK_FB_MESSAGE = range(2)
ASK_ALERT_MESSAGE, CONFIRM_ALERT_BROADCAST = range(10, 12)

_CAT_META = {
    "bug": ("🐞", "Bot Error / Bug"),
    "help": ("❓", "Help Request"),
    "suggestion": ("💡", "Suggestion"),
}


def _category_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🐞 Bot Error / Bug", callback_data="fb_cat|bug"),
            InlineKeyboardButton("❓ Need Help", callback_data="fb_cat|help"),
        ],
        [
            InlineKeyboardButton("💡 Idea / Request", callback_data="fb_cat|suggestion"),
            InlineKeyboardButton("❌ Cancel", callback_data="fb_cat|cancel"),
        ],
    ])


def _alert_preview_text(msg_text: str, recipient_count: int) -> str:
    return (
        f"📢 <b>Preview Team Alert</b>\n\n"
        f"<b>Recipients:</b> {recipient_count} user(s)\n"
        f"<b>Message:</b>\n{esc_html(msg_text)}\n\n"
        "Send this announcement to everyone on Telegram?"
    )


def _alert_preview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Send to All Users", callback_data="alert_send")],
        [
            InlineKeyboardButton("✏️ Edit", callback_data="alert_edit"),
            InlineKeyboardButton("❌ Cancel", callback_data="alert_cancel"),
        ],
    ])


async def _submit_feedback(target, ctx, user, category: str, message_text: str, *, edit: bool = False) -> None:
    stored = get_user(user.id) or {}
    full_name = (
        stored.get("name")
        or f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '') or ''}".strip()
        or f"User {user.id}"
    )
    role = stored.get("role") or "agent"
    username = getattr(user, "username", "") or stored.get("username", "")

    item = await async_create_feedback(
        user_id=user.id,
        user_name=full_name,
        username=username,
        user_role=role,
        category=category,
        message=message_text,
    )
    icon, label = _CAT_META.get(item["category"], ("🐞", "Bot Report"))
    confirmation = (
        f"✅ <b>{esc_html(label)} submitted!</b>\n\n"
        f"<b>Ticket:</b> <code>{esc_html(item['id'])}</code>\n"
        f"It is now visible in <b>Kurtex Desktop → Bot Reports</b> and our developers/managers have been notified."
    )
    ticket_kb = InlineKeyboardMarkup([[InlineKeyboardButton("View ticket / Add screenshot", callback_data=f"fb_view|{item['id']}")]])
    if edit:
        await safe_edit_message_text(target, confirmation, reply_markup=ticket_kb)
    else:
        await safe_reply_text(target, confirmation, reply_markup=ticket_kb)

    bot = ctx.bot if ctx and getattr(ctx, "bot", None) else target.get_bot()
    all_users = await asyncio.to_thread(get_all_user_dicts)
    notify_ids = [
        u["id"] for u in all_users
        if u.get("role") in ("developer", "super_admin") and u.get("id") != user.id
    ]
    if notify_ids:
        handle = f" (@{esc_html(username)})" if username else ""
        dm = (
            f"{icon} <b>New {esc_html(label)}</b> · <code>{esc_html(item['id'])}</code>\n\n"
            f"<b>From:</b> {esc_html(full_name)}{handle} [{esc_html(role)}]\n"
            f"<b>Message:</b>\n{esc_html(item['message'][:1500])}\n\n"
            "<i>Review and reply from Kurtex Desktop → Bot Reports.</i>"
        )
        sem = asyncio.Semaphore(10)

        async def _send(uid: int):
            async with sem:
                try:
                    await safe_send_message(bot, uid, dm)
                except Exception as exc:
                    logger.warning("Could not notify dev/admin %s about feedback %s: %s", uid, item["id"], exc)

        await asyncio.gather(*(_send(uid) for uid in notify_ids), return_exceptions=True)


# ── /bug, /support, /feedback ──────────────────────────────────────────────────

async def cmd_bug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ctx.user_data.pop("fb_category", None)
    inline_text = " ".join(getattr(ctx, "args", None) or []).strip()
    if inline_text:
        await _submit_feedback(update.message, ctx, user, "bug", inline_text[:3000], edit=False)
        return ConversationHandler.END

    await safe_reply_text(
        update.message,
        "🐞 <b>Report Bot Issue or Ask for Help</b>\n\n"
        "What would you like to send to the developer / manager team?",
        reply_markup=_category_kb(),
    )
    return ASK_FB_CATEGORY


async def cb_bug_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        ctx.user_data.pop("fb_category", None)
        bot = ctx.bot if ctx and getattr(ctx, "bot", None) else query.get_bot()
        await safe_send_message(
            bot,
            query.from_user.id,
            "🐞 <b>Report Bot Issue or Ask for Help</b>\n\n"
            "What would you like to send to the developer / manager team?",
            reply_markup=_category_kb(),
        )
        return ASK_FB_CATEGORY


async def cb_feedback_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        cat = (query.data or "").split("|", 1)[1] if "|" in (query.data or "") else "cancel"
        if cat == "cancel":
            ctx.user_data.pop("fb_category", None)
            await safe_edit_message_text(query, "Cancelled.", parse_mode=None, reply_markup=None)
            return ConversationHandler.END
        cat = cat if cat in _CAT_META else "bug"
        ctx.user_data["fb_category"] = cat
        icon, label = _CAT_META[cat]
        await safe_edit_message_text(
            query,
            f"{icon} <b>{esc_html(label)}</b>\n\n"
            "Describe what happened, any error message, or what help you need:\n"
            "<i>Use /cancel or Cancel to stop.</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="fb_cat|cancel"),
            ]]),
        )
        return ASK_FB_MESSAGE


async def recv_feedback_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cat = ctx.user_data.pop("fb_category", "bug")
    text = (update.message.text or "").strip()
    if not text:
        await safe_reply_text(update.message, "Please describe the issue or question, or send /cancel.", parse_mode=None)
        ctx.user_data["fb_category"] = cat
        return ASK_FB_MESSAGE
    await _submit_feedback(update.message, ctx, update.effective_user, cat, text[:3000], edit=False)
    return ConversationHandler.END


async def cmd_feedback_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("fb_category", None)
    await safe_reply_text(update.message, "Cancelled.", parse_mode=None)
    return ConversationHandler.END


def get_feedback_conversation() -> ConversationHandler:
    private_text = filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        name="kurtex_feedback_v1",
        persistent=False,
        entry_points=[
            CommandHandler("bug", cmd_bug, filters=filters.ChatType.PRIVATE),
            CommandHandler("support", cmd_bug, filters=filters.ChatType.PRIVATE),
            CommandHandler("feedback", cmd_bug, filters=filters.ChatType.PRIVATE),
            CallbackQueryHandler(cb_bug_start, pattern=r"^bug_start$"),
        ],
        states={
            ASK_FB_CATEGORY: [
                CallbackQueryHandler(cb_feedback_category, pattern=r"^fb_cat\|"),
            ],
            ASK_FB_MESSAGE: [
                MessageHandler(private_text, recv_feedback_message),
                CallbackQueryHandler(cb_feedback_category, pattern=r"^fb_cat\|"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_feedback_cancel),
            CallbackQueryHandler(cb_feedback_category, pattern=r"^fb_cat\|cancel$"),
        ],
        per_message=False,
        allow_reentry=True,
    )


# ── /alert (developers & super_admins only) ────────────────────────────────────

async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not has_role(user.id, "developer", "super_admin"):
        await safe_reply_text(
            update.message,
            "⛔ Only developers and managers can broadcast team alerts with /alert.",
            parse_mode=None,
        )
        return ConversationHandler.END

    inline_text = " ".join(getattr(ctx, "args", None) or []).strip()
    if inline_text:
        msg_text = inline_text[:3500]
        ctx.user_data["alert_draft_text"] = msg_text
        all_users = await asyncio.to_thread(get_all_user_dicts)
        await safe_reply_text(
            update.message,
            _alert_preview_text(msg_text, len(all_users)),
            reply_markup=_alert_preview_kb(),
        )
        return CONFIRM_ALERT_BROADCAST

    ctx.user_data.pop("alert_draft_text", None)
    await safe_reply_text(
        update.message,
        "📢 <b>Broadcast Team Alert</b>\n\n"
        "Send the announcement message to alert all authorized users "
        "(for example: upcoming update, bot restart notice, or maintenance).\n\n"
        "<i>Use /cancel or Cancel to abort.</i>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="alert_cancel"),
        ]]),
    )
    return ASK_ALERT_MESSAGE


async def recv_alert_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not has_role(user.id, "developer", "super_admin"):
        ctx.user_data.pop("alert_draft_text", None)
        return ConversationHandler.END

    msg_text = (update.message.text or "").strip()
    if not msg_text:
        await safe_reply_text(update.message, "Message cannot be empty. Send the alert text or /cancel.", parse_mode=None)
        return ASK_ALERT_MESSAGE

    msg_text = msg_text[:3500]
    ctx.user_data["alert_draft_text"] = msg_text
    all_users = await asyncio.to_thread(get_all_user_dicts)
    await safe_reply_text(
        update.message,
        _alert_preview_text(msg_text, len(all_users)),
        reply_markup=_alert_preview_kb(),
    )
    return CONFIRM_ALERT_BROADCAST


async def cb_alert_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if not has_role(user.id, "developer", "super_admin"):
        await query.answer("Not authorized.", show_alert=True)
        return ConversationHandler.END

    action = (query.data or "").strip()
    if action == "alert_cancel":
        async with concurrent_ack(query):
            ctx.user_data.pop("alert_draft_text", None)
            await safe_edit_message_text(query, "Team alert cancelled.", parse_mode=None, reply_markup=None)
            return ConversationHandler.END

    if action == "alert_edit":
        async with concurrent_ack(query):
            await safe_edit_message_text(
                query,
                "✏️ Send the updated team alert message:\n<i>Use /cancel or Cancel to abort.</i>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data="alert_cancel"),
                ]]),
            )
            return ASK_ALERT_MESSAGE

    if action == "alert_send":
        msg_text = (ctx.user_data.pop("alert_draft_text", "") or "").strip()
        if not msg_text:
            async with concurrent_ack(query):
                await safe_edit_message_text(query, "Alert session expired. Run /alert again.", parse_mode=None, reply_markup=None)
            return ConversationHandler.END

        async with concurrent_ack(query, "Sending team alert..."):
            stored = get_user(user.id) or {}
            sender_name = (
                stored.get("name")
                or f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '') or ''}".strip()
                or "Admin"
            )
            all_users = await asyncio.to_thread(get_all_user_dicts)
            formatted = (
                f"📢 <b>Kurtex Team Alert</b>\n\n"
                f"{esc_html(msg_text)}\n\n"
                f"<i>— {esc_html(sender_name)}</i>"
            )
            bot = ctx.bot if ctx and getattr(ctx, "bot", None) else query.get_bot()
            sem = asyncio.Semaphore(15)
            delivered = 0
            failed: list[str] = []

            async def _push(u: dict):
                nonlocal delivered
                uid = u["id"]
                async with sem:
                    try:
                        await safe_send_message(bot, uid, formatted)
                        delivered += 1
                    except Exception as exc:
                        logger.warning("Broadcast /alert failed for user %s: %s", uid, exc)
                        failed.append(u.get("name") or str(uid))

            await asyncio.gather(*(_push(u) for u in all_users), return_exceptions=True)

            summary = f"✅ <b>Team alert sent!</b>\n\nDelivered to <b>{delivered}/{len(all_users)}</b> user(s)."
            if failed:
                summary += f"\n⚠️ Could not deliver to: {esc_html(', '.join(failed[:10]))}"
            await safe_edit_message_text(query, summary, reply_markup=None)
            logger.info("Broadcast /alert sent by %s to %d/%d users", user.id, delivered, len(all_users))
            return ConversationHandler.END

    return CONFIRM_ALERT_BROADCAST


async def cmd_alert_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("alert_draft_text", None)
    await safe_reply_text(update.message, "Team alert cancelled.", parse_mode=None)
    return ConversationHandler.END


def get_alert_broadcast_conversation() -> ConversationHandler:
    private_text = filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        name="kurtex_alert_broadcast_v1",
        persistent=False,
        entry_points=[CommandHandler("alert", cmd_alert, filters=filters.ChatType.PRIVATE)],
        states={
            ASK_ALERT_MESSAGE: [
                MessageHandler(private_text, recv_alert_message),
                CallbackQueryHandler(cb_alert_action, pattern=r"^alert_"),
            ],
            CONFIRM_ALERT_BROADCAST: [
                CallbackQueryHandler(cb_alert_action, pattern=r"^alert_"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_alert_cancel),
            CallbackQueryHandler(cb_alert_action, pattern=r"^alert_cancel$"),
        ],
        per_message=False,
        allow_reentry=True,
    )


# ── My bot reports / ticket conversations (private chat only) ────────────────
ASK_TICKET_UPDATE = 21


def _ticket_keyboard(ticket):
    if ticket["status"] == "resolved":
        return InlineKeyboardMarkup([[InlineKeyboardButton("Back to my reports", callback_data="fb_list|all")]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Add information / Screenshot", callback_data=f"fb_reply|{ticket['id']}")],
        [InlineKeyboardButton("Back to my reports", callback_data="fb_list|all")],
    ])


async def _show_my_reports(update, ctx):
    from backend.storage.feedback_store import list_my_feedback
    user = update.effective_user
    reports = await asyncio.to_thread(list_my_feedback, user.id, 12)
    if not reports:
        content = "📋 <b>Your bot reports</b>\n\nNo reports yet. Send /bug or /support to open one."
        keyboard = None
    else:
        content = "📋 <b>Your bot reports</b>\n\nSelect a ticket to read updates, ask questions, or add a screenshot."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"{'✅' if item['status']=='resolved' else '🔴'} {str(item['message'] or 'Issue')[:32]}",
                callback_data=f"fb_view|{item['id']}",
            )] for item in reports
        ])
    if update.callback_query:
        await safe_send_message(ctx.bot, user.id, content, reply_markup=keyboard)
    else:
        await safe_reply_text(update.message, content, reply_markup=keyboard)


async def cmd_myreports(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _show_my_reports(update, ctx)
    return ConversationHandler.END


async def cb_myreport(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from backend.storage.feedback_store import get_feedback, list_feedback_messages
    query = update.callback_query
    async with concurrent_ack(query):
        action, _, ticket_id = (query.data or "").partition("|")
        if action == "fb_list":
            ctx.user_data.pop("feedback_active_ticket", None)
            await _show_my_reports(update, ctx)
            return ConversationHandler.END
        ticket = await asyncio.to_thread(get_feedback, ticket_id)
        if not ticket or ticket["user_id"] != query.from_user.id:
            await safe_send_message(ctx.bot, query.from_user.id, "This report is not available to your account.", parse_mode=None)
            return ConversationHandler.END
        if action == "fb_reply":
            if ticket["status"] == "resolved":
                await safe_send_message(ctx.bot, query.from_user.id, "This ticket is resolved and read-only.", parse_mode=None)
                return ConversationHandler.END
            ctx.user_data["feedback_active_ticket"] = ticket_id
            await safe_send_message(ctx.bot, query.from_user.id,
                "📎 <b>Add details to your report</b>\n\nSend a message, screenshot, or PDF (up to 10 MB). "
                "You can send multiple updates. Use /cancel when done.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Finish adding details", callback_data=f"fb_done|{ticket_id}")
                ]]))
            return ASK_TICKET_UPDATE
        messages = await asyncio.to_thread(list_feedback_messages, ticket_id, 15)
        preview = [
            f"📋 <b>{esc_html(ticket['category'].title())} report</b> — "
            f"{esc_html(ticket['status'].replace('_',' ').title())}",
            f"\n<b>Your report:</b>\n{esc_html(ticket['message'][:1300])}",
        ]
        if ticket.get("admin_note"):
            preview.append(f"\n<b>Latest staff response:</b>\n{esc_html(ticket['admin_note'][:900])}")
        if messages:
            preview.append("\n<b>Conversation:</b>")
            for msg in messages[-7:]:
                who = "Support" if msg["sender_role"] == "manager" else "You"
                body = (msg["message"] or "")[:350] or ("📎 " + (msg["file_name"] or "Screenshot"))
                preview.append(f"• <b>{who}:</b> {esc_html(body)}")
        if ticket["status"] == "resolved":
            preview.append("\n🔒 <i>This ticket is resolved. No further changes can be made.</i>")
        await safe_send_message(ctx.bot, query.from_user.id,
                                "\n".join(preview), reply_markup=_ticket_keyboard(ticket))
        return ConversationHandler.END


async def recv_ticket_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from backend.storage.feedback_store import add_feedback_message, get_feedback
    ticket_id = ctx.user_data.get("feedback_active_ticket")
    if not ticket_id:
        return ConversationHandler.END
    user = update.effective_user
    ticket = await asyncio.to_thread(get_feedback, ticket_id)
    if not ticket or ticket["user_id"] != user.id or ticket["status"] == "resolved":
        ctx.user_data.pop("feedback_active_ticket", None)
        await safe_reply_text(update.message, "This ticket is unavailable or already resolved.", parse_mode=None)
        return ConversationHandler.END
    msg = update.message
    body = (msg.text or msg.caption or "").strip()[:3000]
    media_type = file_id = name = mime = ""
    size = 0
    if msg.photo:
        image = msg.photo[-1]
        media_type, file_id, name, mime, size = "photo", image.file_id, "Screenshot.jpg", "image/jpeg", image.file_size or 0
    elif msg.document:
        doc = msg.document
        mime = str(doc.mime_type or "").lower()
        if mime not in ("image/png", "image/jpeg", "image/webp", "application/pdf"):
            await safe_reply_text(msg, "Send a PNG/JPG/WebP screenshot or a PDF, please.", parse_mode=None)
            return ASK_TICKET_UPDATE
        media_type, file_id, name, size = "document", doc.file_id, (doc.file_name or "attachment")[:120], doc.file_size or 0
    if size > 10 * 1024 * 1024:
        await safe_reply_text(msg, "Attachment is too large. The limit is 10 MB.", parse_mode=None)
        return ASK_TICKET_UPDATE
    if not body and not file_id:
        await safe_reply_text(msg, "Send text or an image/document to continue.", parse_mode=None)
        return ASK_TICKET_UPDATE
    try:
        await asyncio.to_thread(add_feedback_message, ticket_id,
            author_id=str(user.id), author_name=user.full_name or str(user.id),
            sender_role="reporter", message=body, media_type=media_type,
            telegram_file_id=file_id, file_name=name, mime_type=mime)
    except (LookupError, ValueError, PermissionError) as exc:
        await safe_reply_text(msg, str(exc), parse_mode=None)
        return ConversationHandler.END

    await safe_reply_text(msg, "✅ Added to your report. Send more details, or tap Finish.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Finish", callback_data=f"fb_done|{ticket_id}")
        ]]))
    recipients = await asyncio.to_thread(get_all_user_dicts)
    staff_ids = [u["id"] for u in recipients if u.get("role") in ("developer", "super_admin") and u["id"] != user.id]
    summary = f"💬 <b>New information on bot report</b>\n<b>From:</b> {esc_html(user.full_name)}\n"
    summary += esc_html(body[:600] if body else "📎 Screenshot / attachment")
    summary += "\n<i>Review in Kurtex Desktop → Bot Reports.</i>"
    await asyncio.gather(*(safe_send_message(ctx.bot, uid, summary) for uid in staff_ids), return_exceptions=True)
    return ASK_TICKET_UPDATE


async def cb_ticket_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with concurrent_ack(update.callback_query):
        ctx.user_data.pop("feedback_active_ticket", None)
        await safe_send_message(ctx.bot, update.effective_user.id,
                                "✅ Details saved. Use /myreports to check the conversation.", parse_mode=None)
    return ConversationHandler.END


async def cmd_ticket_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("feedback_active_ticket", None)
    await safe_reply_text(update.message, "Stopped adding details. Your earlier messages are saved.", parse_mode=None)
    return ConversationHandler.END


def get_ticket_conversation() -> ConversationHandler:
    allowed = filters.ChatType.PRIVATE
    return ConversationHandler(
        name="kurtex_ticket_thread_v1",
        persistent=False,
        entry_points=[
            CommandHandler("myreports", cmd_myreports, filters=allowed),
            CommandHandler("tickets", cmd_myreports, filters=allowed),
            CallbackQueryHandler(cb_myreport, pattern=r"^fb_(view|reply|list)\|"),
        ],
        states={
            ASK_TICKET_UPDATE: [
                MessageHandler(allowed & (filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, recv_ticket_update),
                CallbackQueryHandler(cb_ticket_finish, pattern=r"^fb_done\|"),
                CallbackQueryHandler(cb_myreport, pattern=r"^fb_(view|reply|list)\|"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_ticket_cancel)],
        allow_reentry=True,
        per_message=False,
    )
