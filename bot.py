"""
Kurtex Alert Bot — Truck Maintenance Command Center
"""
import os
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, TypeHandler,
    ApplicationHandlerStop
)

from config import config
from shifts import ADMINS, MAIN_ADMIN_ID
from handlers.alert_handler import AlertHandler, TRIGGER_WORDS
from handlers.report_handler import get_report_conversation
from handlers.agent_handler import (
    cmd_done, cmd_mycases, cmd_casehistory, cb_done_pick,
    cb_solve_confirm, cb_solve_cancel,
    cb_delete_confirm, cb_delete_do, cb_delete_keep,
    cb_histpage, cb_hist_delete_chat, get_solve_conversation
)
from handlers.admin_handler import cmd_report, cmd_leaderboard, cmd_missed, _is_main_admin
from handlers.scheduler import register_jobs
from user_tracker import has_user_started, mark_user_started

BOT_NAME    = "Kurtex Alert Bot"
BOT_TAGLINE = "Truck Maintenance Command Center"

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG  # Changed to DEBUG to see all logs
)
logger = logging.getLogger(__name__)


# ── Debug handler ───────────────────────────────────────────────────────────

async def debug_all_updates(update: Update, ctx):
    """Catch-all to debug ALL updates"""
    logger.warning(f"DEBUG ALL: Received update: {update}")
    if update.message:
        logger.warning(f"DEBUG ALL: Message: {update.message.text}, Chat: {update.effective_chat.type if update.effective_chat else 'None'}, User: {update.effective_user.id if update.effective_user else 'None'}")


# ── Auth middleware ───────────────────────────────────────────────────────────

async def auth_middleware(update: Update, ctx):
    user = update.effective_user
    if not user:
        logger.info("Auth: no user, ignoring")
        return
    
    chat = update.effective_chat
    chat_type = chat.type if chat else "none"
    logger.info(f"Auth: user={user.id}(@{user.username}), chat_type={chat_type}")
    
    # Allow group chats to pass through for trigger detection
    if chat and chat.type in ("group", "supergroup"):
        logger.info(f"Auth: allowing group message")
        return
    
    # Check if user is admin
    if user.id not in ADMINS and user.id != MAIN_ADMIN_ID:
        logger.info(f"Auth: user {user.id} not authorized")
        if update.message:
            await update.message.reply_text(
                "You are not authorized to use this bot.\n"
                "Contact an administrator for access."
            )
        raise ApplicationHandlerStop
    
    logger.info(f"Auth: user {user.id} authorized")


# ── Startup ───────────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    from shifts import SUPER_ADMINS, ADMINS
    from telegram import BotCommandScopeChat
    
    # Reset webhook to allow all update types
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook deleted with drop_pending_updates=True to reset allowed_updates")
    except Exception as e:
        logger.warning(f"Could not reset webhook: {e}")
    
    # Set webhook if configured (for Railway/production)
    if config.USE_WEBHOOK:
        webhook_url = f"{config.WEBHOOK_URL}/webhook"
        await application.bot.set_webhook(
            url=webhook_url,
            secret_token=config.WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )
        logger.info(f"Webhook set to: {webhook_url}")
    
    # Debug: log loaded admins
    logger.info(f"DEBUG: ADMINS loaded: {list(ADMINS.keys())}")
    logger.info(f"DEBUG: SUPER_ADMINS loaded: {SUPER_ADMINS}")
    logger.info(f"DEBUG: MAIN_ADMIN_ID: {MAIN_ADMIN_ID}")

    base_commands = [
        ("start",       "Register with Kurtex Alert Bot"),
        ("shifts",      "View current shift roster"),
        ("mycases",     "Your active cases"),
        ("done",        "Today's closed cases"),
        ("casehistory", "Full closed case history"),
        ("help",        "Bot commands and help"),
    ]

    super_commands = base_commands + [
        ("report",      "Daily summary"),
        ("leaderboard", "Top performers"),
        ("missed",      "Missed alerts"),
    ]

    # Default commands for all admins
    await application.bot.set_my_commands(base_commands)

    # Override for each super admin
    for admin_id in SUPER_ADMINS:
        try:
            await application.bot.set_my_commands(
                super_commands,
                scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as e:
            logger.warning(f"Could not set commands for super admin {admin_id}: {e}")

    me = await application.bot.get_me()
    logger.info(f"{BOT_NAME} started as @{me.username}")
    logger.info(f"Triggers: {', '.join(TRIGGER_WORDS)}")


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx):
    user = update.effective_user
    if has_user_started(user.id):
        await update.message.reply_text("Already registered. Use /help for commands.")
        return
    mark_user_started(user.id)
    await update.message.reply_text(
        f"Welcome to {BOT_NAME}!\n\n"
        f"{BOT_TAGLINE}\n\n"
        "You are now registered and will receive alerts during your shift.\n\n"
        "Quick start:\n"
        "/shifts — See who is on duty\n"
        "/help — View all commands"
    )


async def cmd_shifts(update: Update, ctx):
    from shift_manager import get_on_shift_admins, get_current_shift_name
    shift_name = get_current_shift_name()
    on_shift   = get_on_shift_admins()
    if on_shift:
        names = "\n".join(
            f"  {a['name']} (@{a['username']})" if a['username']
            else f"  {a['name']}"
            for a in on_shift
        )
        await update.message.reply_text(f"Shift: {shift_name}\n\nOn duty:\n{names}")
    else:
        await update.message.reply_text(
            f"Shift: {shift_name}\n\nNo agents scheduled. All admins will be notified."
        )


async def cmd_help(update: Update, ctx):
    user     = update.effective_user
    is_super = _is_main_admin(user.id)
    words    = "  ".join(TRIGGER_WORDS)

    text = (
        f"{BOT_NAME}\n"
        f"{BOT_TAGLINE}\n\n"
        "Driver reporting — post in driver group:\n"
        f"{words}\n\n"
        "Example: #maintenance engine overheating, truck 42\n\n"
        "Agent commands:\n"
        "/mycases — Your active cases\n"
        "/done — Today's closed cases\n"
        "/casehistory — Full case history\n"
        "/shifts — Who is on duty\n"
    )

    if is_super:
        text += (
            "\nSuper admin commands:\n"
            "/report — Daily summary\n"
            "/leaderboard — Top performers\n"
            "/missed — Unhandled alerts\n"
        )

    await update.message.reply_text(text)


# ── Main ──────────────────────────────────────────────────────────────────────


async def reset_polling_offset(app):
    """Get the latest update ID WITHOUT consuming updates"""
    logger.info("Getting latest update ID without consuming...")
    try:
        # Get updates with a high offset to find the latest
        # This does NOT mark them as read - we're just peeking
        updates = await app.bot.get_updates(offset=2147483647, limit=1)
        if updates:
            # Set offset to latest + 1 so we start AFTER it
            next_offset = updates[-1].update_id + 1
            logger.info(f"Latest update ID: {updates[-1].update_id}, will start from {next_offset}")
        else:
            logger.info("No updates found on Telegram")
    except Exception as e:
        logger.error(f"Error getting update ID: {e}")

def main():
    alert_h = AlertHandler()

    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )
    
    # Note: reset_polling_offset removed - not needed in webhook mode, causes conflicts
    
    # Add error handler to log any errors
    async def error_handler(update: Update, ctx):
        logger.error(f"Exception while handling update: {ctx.error}", exc_info=ctx.error)
    
    app.add_error_handler(error_handler)

    app.bot_data["alert_handler"] = alert_h

    # Debug catch-all handler — runs first to log ALL updates
    app.add_handler(TypeHandler(Update, debug_all_updates), group=-2)

    # Auth middleware — runs before everything
    app.add_handler(TypeHandler(Update, auth_middleware), group=-1)

    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("shifts",       cmd_shifts))
    app.add_handler(CommandHandler("help",         cmd_help))

    # Agent commands
    app.add_handler(CommandHandler("done",         cmd_done))
    app.add_handler(CommandHandler("mycases",      cmd_mycases))
    app.add_handler(CommandHandler("casehistory",  cmd_casehistory))

    # Admin commands (super admin only — enforced in handlers)
    app.add_handler(CommandHandler("report",       cmd_report))
    app.add_handler(CommandHandler("leaderboard",  cmd_leaderboard))
    app.add_handler(CommandHandler("missed",       cmd_missed))

    # ── Conversation handlers (must be before standalone CallbackQueryHandlers)
    app.add_handler(get_solve_conversation())
    app.add_handler(get_report_conversation())

    # ── Trigger word detection ────────────────────────────────────────────────
    trigger_pattern = '|'.join(TRIGGER_WORDS).replace('#', r'\#')
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS &
        (filters.TEXT | filters.PHOTO) &
        filters.Regex(f'(?i)({trigger_pattern})'),
        alert_h.handle
    ))

    # ── AI Alerts channel listener ───────────────────────────────────────────
    app.add_handler(MessageHandler(
        filters.ChatType.CHANNEL & filters.TEXT,
        alert_h.handle_channel_post
    ))

    # ── Button callbacks ──────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(alert_h.handle_assignment, pattern=r'^(assign|assignrpt|ignore)\|'))
    app.add_handler(CallbackQueryHandler(alert_h.handle_reassign,   pattern=r'^reassign_'))
    app.add_handler(CallbackQueryHandler(cb_done_pick,      pattern=r'^done_pick\|'))
    app.add_handler(CallbackQueryHandler(cb_solve_confirm,          pattern=r'^solve_confirm\|'))
    app.add_handler(CallbackQueryHandler(cb_solve_cancel,           pattern=r'^solve_cancel\|'))
    app.add_handler(CallbackQueryHandler(cb_delete_confirm,         pattern=r'^delete_confirm\|'))
    app.add_handler(CallbackQueryHandler(cb_delete_do,              pattern=r'^delete_do\|'))
    app.add_handler(CallbackQueryHandler(cb_delete_keep,            pattern=r'^delete_keep\|'))
    app.add_handler(CallbackQueryHandler(cb_histpage,               pattern=r'^histpage\|'))
    app.add_handler(CallbackQueryHandler(cb_hist_delete_chat,       pattern=r'^hist_delete_chat$'))

    # ── Scheduled jobs ────────────────────────────────────────────────────────
    register_jobs(app)

    logger.info(f"Starting {BOT_NAME}...")
    
    # Webhook mode is recommended for production (Railway) - avoids getUpdates conflicts
    # Set WEBHOOK_URL env var to enable (e.g., https://your-app.railway.app)
    if config.USE_WEBHOOK:
        logger.info(f"Using webhook mode: {config.WEBHOOK_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", 8080)),
            url_path="webhook",
            webhook_url=config.WEBHOOK_URL,
            secret_token=config.WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )
    else:
        # Polling mode for local development
        app.run_polling(drop_pending_updates=False, allowed_updates=[])


if __name__ == '__main__':
    main()

