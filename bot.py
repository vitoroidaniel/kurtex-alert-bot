"""
Kurtex Alert Bot — Truck Maintenance Command Center
Dynamic user management via Telegram commands + forward-to-add flow.
"""

import asyncio
import logging
import os
import signal

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, TypeHandler,
    ApplicationHandlerStop,
)

from config import config
from shifts import ADMINS, SUPER_ADMINS
from handlers.alert_handler import AlertHandler, TRIGGER_WORDS
from handlers.report_handler import get_report_conversation
from handlers.agent_handler import (
    cmd_done, cmd_mycases, cmd_mystats, cmd_casehistory,
    cb_done_pick, cb_solve_confirm, cb_solve_cancel,
    cb_delete_confirm, cb_delete_do, cb_delete_keep,
    cb_close_confirm, cb_close_cancel,
    cb_histpage, cb_hist_delete_chat, get_solve_conversation,
    cb_solve_start, cb_close_ask,
)
from handlers.admin_handler import (
    cmd_report, cmd_leaderboard, cmd_missed, _is_main_admin,
    cmd_adduser, cmd_removeuser, cmd_editrole, cmd_listusers,
    handle_forward, cb_addrole,
)
from handlers.scheduler import register_jobs
from storage.user_store import (
    is_authorized, bootstrap_developer, migrate_from_shifts,
    has_role,
)

BOT_NAME    = "Kurtex Alert Bot"
BOT_TAGLINE = "Truck Maintenance Command Center"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Typing decorator ──────────────────────────────────────────────────────────

def with_typing(fn):
    async def wrapper(update: Update, ctx):
        if update.effective_chat:
            try:
                await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            except Exception:
                pass
        return await fn(update, ctx)
    wrapper.__name__ = fn.__name__
    return wrapper


# ── Auth middleware ───────────────────────────────────────────────────────────

async def auth_middleware(update: Update, ctx):
    user = update.effective_user
    if not user:
        return
    chat = update.effective_chat

    # Allow group messages through (alert triggers, etc.)
    if chat and chat.type in ("group", "supergroup"):
        msg = update.effective_message
        if msg and msg.text and msg.text.startswith("/"):
            raise ApplicationHandlerStop
        return

    # Private chat — must be in user_store
    if not is_authorized(user.id):
        if update.message:
            await update.message.reply_text(
                "⛔ You are not authorized to use this bot.\n"
                "Contact an administrator for access."
            )
        raise ApplicationHandlerStop


# ── Startup ───────────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    from telegram import BotCommandScopeChat

    # ── Migrate existing hardcoded admins on first boot ──
    migrate_from_shifts(ADMINS, SUPER_ADMINS)

    # ── Bootstrap developer from env var ──
    dev_id_str = os.getenv("DEVELOPER_ID", "").strip()
    if dev_id_str:
        try:
            bootstrap_developer(int(dev_id_str), os.getenv("DEVELOPER_NAME", "Developer"))
        except ValueError:
            logger.warning(f"DEVELOPER_ID is not a valid integer: {dev_id_str!r}")

    # Reload unassigned alerts from disk so admins can still accept after restart
    alert_h = application.bot_data.get("alert_handler")
    if alert_h:
        alert_h.load_from_disk()

    base_commands = [
        ("start",       "Register with Kurtex Alert Bot"),
        ("shifts",      "Current shift roster"),
        ("mycases",     "Your active cases"),
        ("done",        "Today's closed cases"),
        ("casehistory", "Full closed case history"),
        ("mystats",     "Your performance stats"),
        ("help",        "Commands and help"),
    ]
    manager_commands = base_commands + [
        ("report",      "Daily summary"),
        ("leaderboard", "Weekly top performers"),
        ("missed",      "Unhandled alerts today"),
        ("listusers",   "List all users and roles"),
        ("adduser",     "Add user by ID"),
        ("removeuser",  "Remove a user"),
        ("editrole",    "Change a user's role"),
    ]

    await application.bot.set_my_commands(base_commands)

    # Set extended command list for developer + super_admin
    from storage.user_store import get_all_users
    for uid_str, u in get_all_users().items():
        if u["role"] in ("developer", "super_admin"):
            try:
                await application.bot.set_my_commands(
                    manager_commands,
                    scope=BotCommandScopeChat(chat_id=int(uid_str)),
                )
            except Exception as e:
                logger.warning(f"Could not set commands for {uid_str}: {e}")

    me = await application.bot.get_me()
    logger.info(f"{BOT_NAME} started as @{me.username}")


# ── Commands ──────────────────────────────────────────────────────────────────

@with_typing
async def cmd_start(update: Update, ctx):
    """
    /start now just welcomes the user.
    If they're already in user_store (added by forward or /adduser), they're good.
    If not in store but reached here (shouldn't happen due to auth_middleware),
    we auto-add them as agent so they don't get locked out.
    """
    from storage.user_store import get_user, add_user
    user    = update.effective_user
    stored  = get_user(user.id)

    if not stored:
        # Edge case: user somehow bypassed auth — auto-register as agent
        name = f"{user.first_name} {user.last_name or ''}".strip()
        add_user(user.id, name, user.username or "", "agent")
        stored = get_user(user.id)

    role = stored["role"] if stored else "agent"
    await update.message.reply_text(
        f"👋 Welcome to *{BOT_NAME}!*\n\n_{BOT_TAGLINE}_\n\n"
        f"You're registered as *{role}*.\n\n"
        "/shifts — See who is on duty\n"
        "/help — All commands",
        parse_mode="Markdown",
    )


@with_typing
async def cmd_shifts(update: Update, ctx):
    from shift_manager import get_on_shift_admins, get_current_shift_name
    shift_name = get_current_shift_name()
    on_shift   = get_on_shift_admins()

    if not on_shift:
        await update.message.reply_text(
            f"Shift: {shift_name}\n\nNo agents scheduled. All admins will be notified."
        )
        return

    names = "\n".join(
        f"  {a['name']} (@{a['username']})" if a["username"] else f"  {a['name']}"
        for a in on_shift
    )
    await update.message.reply_text(f"Shift: {shift_name}\n\nOn duty:\n{names}")


@with_typing
async def cmd_help(update: Update, ctx):
    user     = update.effective_user
    is_super = _is_main_admin(user.id)
    is_dev   = has_role(user.id, "developer")
    words    = "  ".join(TRIGGER_WORDS)

    text = (
        f"*{BOT_NAME}*\n_{BOT_TAGLINE}_\n\n"
        "📢 *Driver reporting* — post in driver group:\n"
        f"`{words}`\n\n"
        "_Example: #maintenance engine overheating, truck 42_\n\n"
        "*Agent commands:*\n"
        "/mycases — Active cases\n"
        "/done — Today's closed cases\n"
        "/casehistory — Full history\n"
        "/mystats — Your stats\n"
        "/shifts — Shift roster\n"
    )
    if is_super:
        text += (
            "\n*Admin commands:*\n"
            "/report — Daily summary\n"
            "/leaderboard — Weekly top performers\n"
            "/missed — Unhandled alerts\n"
            "/listusers — All users and roles\n"
            "/adduser — Add user by ID and role\n"
            "/removeuser — Remove a user\n"
            "/editrole — Change a user's role\n"
            "\n💡 *Tip:* Forward any message from a user to add them quickly.\n"
        )
    if is_dev and not is_super:
        text += (
            "\n*Developer commands:*\n"
            "/listusers — All users and roles\n"
            "/adduser — Add user by ID and role\n"
            "/removeuser — Remove a user\n"
            "/editrole — Change a user's role\n"
            "\n💡 *Tip:* Forward any message from a user to add them quickly.\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── SIGTERM handler ───────────────────────────────────────────────────────────

def _register_sigterm(application: Application):
    def _handle(signum, frame):
        logger.info("SIGTERM — notifying admins mid-conversation")

        async def _notify():
            try:
                for uid, udata in application.user_data.items():
                    if udata.get("report_case_id"):
                        try:
                            await application.bot.send_message(
                                uid,
                                "⚠️ *Bot is restarting.*\n\n"
                                "Your in-progress report was not saved.\n"
                                "Use /mycases when the bot comes back online.",
                                parse_mode="Markdown",
                            )
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"SIGTERM notify error: {e}")

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_notify())
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _handle)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    alert_h = AlertHandler()

    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    import handlers.agent_handler as _ah
    _ah._bot_ref = app.bot

    async def error_handler(update, ctx):
        logger.error(f"Update error: {ctx.error}", exc_info=ctx.error)

    app.add_error_handler(error_handler)
    app.bot_data["alert_handler"] = alert_h
    _register_sigterm(app)

    app.add_handler(TypeHandler(Update, auth_middleware), group=-1)

    private = filters.ChatType.PRIVATE

    # ── Core commands ──
    app.add_handler(CommandHandler("start",       cmd_start,       filters=private))
    app.add_handler(CommandHandler("shifts",      cmd_shifts,      filters=private))
    app.add_handler(CommandHandler("help",        cmd_help,        filters=private))
    app.add_handler(CommandHandler("done",        cmd_done,        filters=private))
    app.add_handler(CommandHandler("mycases",     cmd_mycases,     filters=private))
    app.add_handler(CommandHandler("casehistory", cmd_casehistory, filters=private))
    app.add_handler(CommandHandler("mystats",     cmd_mystats,     filters=private))

    # ── Admin report commands ──
    app.add_handler(CommandHandler("report",      cmd_report,      filters=private))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard, filters=private))
    app.add_handler(CommandHandler("missed",      cmd_missed,      filters=private))

    # ── User management commands ──
    app.add_handler(CommandHandler("adduser",    cmd_adduser,    filters=private))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser, filters=private))
    app.add_handler(CommandHandler("editrole",   cmd_editrole,   filters=private))
    app.add_handler(CommandHandler("listusers",  cmd_listusers,  filters=private))

    # ── Forward-to-add: catches forwarded messages in private chat ──
    app.add_handler(MessageHandler(private & filters.FORWARDED, handle_forward))

    # ── Role selection callback (from forward flow) ──
    app.add_handler(CallbackQueryHandler(cb_addrole, pattern=r"^addrole\|"))

    app.add_handler(get_solve_conversation())
    app.add_handler(get_report_conversation())

    import re as _re
    def _build_pattern(words):
        return '|'.join(
            _re.escape(w) if w.startswith('#') else r'\b' + _re.escape(w) + r'\b'
            for w in words
        )

    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & (filters.TEXT | filters.PHOTO) &
        filters.Regex(f'(?i)({_build_pattern(TRIGGER_WORDS)})'),
        alert_h.handle,
    ))

    app.add_handler(MessageHandler(
        filters.ChatType.CHANNEL & filters.TEXT,
        alert_h.handle_channel_post,
    ))

    app.add_handler(CallbackQueryHandler(alert_h.handle_assignment,  pattern=r'^(assign|assignrpt|ignore)\|'))
    app.add_handler(CallbackQueryHandler(alert_h.handle_reassign,    pattern=r'^reassign_'))
    app.add_handler(CallbackQueryHandler(cb_done_pick,               pattern=r'^done_pick\|'))
    app.add_handler(CallbackQueryHandler(cb_solve_start,             pattern=r'^solve\|'))
    app.add_handler(CallbackQueryHandler(cb_close_ask,               pattern=r'^close_ask\|'))
    app.add_handler(CallbackQueryHandler(cb_solve_confirm,           pattern=r'^solve_confirm\|'))
    app.add_handler(CallbackQueryHandler(cb_solve_cancel,            pattern=r'^solve_cancel\|'))
    app.add_handler(CallbackQueryHandler(cb_close_confirm,           pattern=r'^close_confirm\|'))
    app.add_handler(CallbackQueryHandler(cb_close_cancel,            pattern=r'^close_cancel\|'))
    app.add_handler(CallbackQueryHandler(cb_delete_confirm,          pattern=r'^delete_confirm\|'))
    app.add_handler(CallbackQueryHandler(cb_delete_do,               pattern=r'^delete_do\|'))
    app.add_handler(CallbackQueryHandler(cb_delete_keep,             pattern=r'^delete_keep\|'))
    app.add_handler(CallbackQueryHandler(cb_histpage,                pattern=r'^histpage\|'))
    app.add_handler(CallbackQueryHandler(cb_hist_delete_chat,        pattern=r'^hist_delete_chat$'))

    register_jobs(app)

    logger.info(f"Starting {BOT_NAME}...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
