"""
Kurtex Alert Bot — Truck Maintenance Command Center
"""
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
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ── Auth middleware ───────────────────────────────────────────────────────────

async def auth_middleware(update: Update, ctx):
    user = update.effective_user
    if not user:
        return
    
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        return
    
    if user.id not in ADMINS and user.id != MAIN_ADMIN_ID:
        if update.message:
            await update.message.reply_text(
                "You are not authorized to use this bot.\n"
                "Contact an administrator for access."
            )
        raise ApplicationHandlerStop


# ── Startup ───────────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    from shifts import SUPER_ADMINS
    from telegram import BotCommandScopeChat

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

    await application.bot.set_my_commands(base_commands)

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
        "/casehistory — Full closed case history\n"
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

def main():
    alert_h = AlertHandler()

    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.bot_data["alert_handler"] = alert_h

    # Auth middleware
    app.add_handler(TypeHandler(Update, auth_middleware), group=-1)

    # Commands
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("shifts",       cmd_shifts))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("done",         cmd_done))
    app.add_handler(CommandHandler("mycases",      cmd_mycases))
    app.add_handler(CommandHandler("casehistory",  cmd_casehistory))
    app.add_handler(CommandHandler("report",       cmd_report))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("missed",       cmd_missed))

    # Conversation handlers
    app.add_handler(get_solve_conversation())
    app.add_handler(get_report_conversation())

    # Trigger word detection
    trigger_pattern = '|'.join(TRIGGER_WORDS).replace('#', r'\#')
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS &
        (filters.TEXT | filters.PHOTO) &
        filters.Regex(f'(?i)({trigger_pattern})'),
        alert_h.handle
    ))

    # Button callbacks
    app.add_handler(CallbackQueryHandler(alert_h.handle_assignment, pattern=r'^(assign|assignrpt|ignore)\|'))
    app.add_handler(CallbackQueryHandler(alert_h.handle_reassign,   pattern=r'^reassign_'))
    app.add_handler(CallbackQueryHandler(cb_done_pick,      pattern=r'^done_pick\|'))
    app.add_handler(CallbackQueryHandler(cb_solve_confirm,   pattern=r'^solve_confirm\|'))
    app.add_handler(CallbackQueryHandler(cb_solve_cancel,    pattern=r'^solve_cancel\|'))
    app.add_handler(CallbackQueryHandler(cb_delete_confirm, pattern=r'^delete_confirm\|'))
    app.add_handler(CallbackQueryHandler(cb_delete_do,      pattern=r'^delete_do\|'))
    app.add_handler(CallbackQueryHandler(cb_delete_keep,    pattern=r'^delete_keep\|'))
    app.add_handler(CallbackQueryHandler(cb_histpage,       pattern=r'^histpage\|'))
    app.add_handler(CallbackQueryHandler(cb_hist_delete_chat, pattern=r'^hist_delete_chat$'))

    # Scheduled jobs
    register_jobs(app)

    logger.info(f"Starting {BOT_NAME}...")
    # Use polling - explicitly allow ALL updates to fix the issue
    app.run_polling(drop_pending_updates=False, allowed_updates=[])


if __name__ == '__main__':
    main()

