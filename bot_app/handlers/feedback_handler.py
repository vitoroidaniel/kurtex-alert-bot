"""Telegram team-alert broadcast handler. Dashboard handles all bugs and support tickets."""

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

from backend.storage.user_store import get_all_user_dicts, get_user, has_role
from bot_app.telegram_utils import (
    concurrent_ack,
    esc_html,
    safe_edit_message_text,
    safe_reply_text,
    safe_send_message,
)

logger = logging.getLogger(__name__)

ASK_ALERT_MESSAGE, CONFIRM_ALERT_BROADCAST = range(10, 12)

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


