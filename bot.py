"""
Kurtex Alert Bot -- Truck Maintenance Command Center
High-concurrency Telegram bot + SQLite-backed shared API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
from datetime import datetime, timezone
from pathlib import Path

from telegram import BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, Conflict, NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PersistenceInput,
    PicklePersistence,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

from api.server import start_api_thread, stop_api_server
from backend.core.app_time import CENTRAL_TIMEZONE_LABEL, chicago_timestamp
from backend.core.config import config
from backend.core.shift_manager import get_current_shift_name, get_on_shift_admins
from backend.storage.case_store import async_get_untouched_unassigned_cases, ensure_indexes
from backend.storage.shift_store import get_on_shift_state
from backend.storage.user_store import (
    add_user,
    get_all_users,
    get_user,
    has_role,
    is_authorized,
    sync_telegram_username,
)
from bot_app.handlers.admin_handler import (
    _is_main_admin,
    cb_addrole,
    cmd_adduser,
    cmd_editrole,
    cmd_leaderboard,
    cmd_listusers,
    cmd_missed,
    cmd_removeuser,
    cmd_report,
    handle_forward,
)
from bot_app.handlers.agent_handler import (
    cb_close_ask,
    cb_close_cancel,
    cb_close_confirm,
    cb_close_parts_cancel,
    cb_close_parts_no,
    cb_close_parts_skip,
    cb_close_parts_yes,
    cb_delete_confirm,
    cb_delete_do,
    cb_delete_keep,
    cb_done_pick,
    cb_hist_delete_chat,
    cb_histpage,
    cb_mycases_clear,
    cb_mycases_next_case,
    cb_mycases_open,
    cb_mycases_page,
    cb_mycases_view,
    cb_solve_cancel,
    cb_solve_confirm,
    cmd_casehistory,
    cmd_done,
    cmd_mycases,
    cmd_mystats,
    recv_close_parts_text,
)
from bot_app.handlers.alert_handler import AlertHandler, TRIGGER_WORDS
from bot_app.handlers.feedback_handler import (
    get_alert_broadcast_conversation,
    get_feedback_conversation,
    get_ticket_conversation,
)
from bot_app.handlers.report_handler import (
    cb_orphan_report_callback,
    get_report_conversation,
    recover_report_text,
)
from bot_app.handlers.scheduler import register_jobs
from bot_app.telegram_utils import esc_html, safe_answer, safe_reply_text, safe_send_message

BOT_NAME = "Kurtex Alert Bot"
BOT_TAGLINE = "Truck Maintenance Command Center"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_BOT_STARTED_AT = datetime.now(timezone.utc)
_BOT_RUNTIME = {"updates_processed": 0, "callbacks_processed": 0, "last_update_at": None}

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)


def _heartbeat_path() -> Path:
    return Path(config.DATA_DIR) / "bot_runtime.json"


def _write_bot_heartbeat(username: str = "") -> None:
    path = _heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": _BOT_STARTED_AT.isoformat(),
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "last_update_at": _BOT_RUNTIME.get("last_update_at"),
        "updates_processed": int(_BOT_RUNTIME.get("updates_processed", 0)),
        "callbacks_processed": int(_BOT_RUNTIME.get("callbacks_processed", 0)),
        "polling_running": bool(_BOT_RUNTIME.get("polling_running", False)),
        "username": username or "",
        "railway_service": os.getenv("RAILWAY_SERVICE_NAME", ""),
        "railway_environment": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT", ""),
        "railway_deployment_id": os.getenv("RAILWAY_DEPLOYMENT_ID", ""),
    }
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("[HEALTH] Could not write bot heartbeat: %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


async def _bot_heartbeat_loop(application: Application) -> None:
    while True:
        try:
            me = application.bot_data.get("bot_identity") or {}
            _BOT_RUNTIME["polling_running"] = bool(application.updater and application.updater.running)
            await asyncio.to_thread(_write_bot_heartbeat, me.get("username", ""))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[HEALTH] Bot heartbeat failed")
        await asyncio.sleep(20)


async def runtime_update_middleware(update: Update, ctx) -> None:
    _BOT_RUNTIME["updates_processed"] += 1
    _BOT_RUNTIME["last_update_at"] = datetime.now(timezone.utc).isoformat()
    if update.callback_query is not None:
        _BOT_RUNTIME["callbacks_processed"] += 1


def _acquire_instance_lock():
    """Prevent two Railway processes sharing the volume from polling one bot."""
    try:
        import fcntl
        lock_path = Path(config.DATA_DIR) / ".kurtex-bot.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        return handle
    except BlockingIOError:
        logger.critical("Another bot process already owns the polling lock; stopping duplicate instance.")
        return None
    except (ImportError, OSError) as exc:
        logger.warning("Could not create single-instance lock: %s", exc)
        return True


def with_typing(fn):
    """Run command directly without burning a blocking sendChatAction round-trip."""
    async def wrapper(update: Update, ctx):
        return await fn(update, ctx)
    wrapper.__name__ = fn.__name__
    return wrapper


async def callback_trace_middleware(update: Update, ctx) -> None:
    query = update.callback_query
    if query is None:
        return
    user_id = update.effective_user.id if update.effective_user else None
    logger.info("[CALLBACK] RECEIVED data=%r user=%s", query.data, user_id)


async def auth_middleware(update: Update, ctx) -> None:
    user = update.effective_user
    if not user:
        return
    chat = update.effective_chat

    # Fast cached user lookup; only touches SQLite write tx if @username changed.
    stored = get_user(user.id)
    if stored is not None:
        clean_uname = (user.username or "").strip().lstrip("@")
        if stored.get("username", "") != clean_uname:
            try:
                await asyncio.to_thread(sync_telegram_username, user.id, clean_uname)
            except Exception:
                logger.exception("Unable to refresh Telegram username for user=%s", user.id)

    if chat and chat.type in ("group", "supergroup"):
        msg = update.effective_message
        if msg and msg.text and msg.text.startswith("/"):
            raise ApplicationHandlerStop
        return

    if stored is None:
        if update.callback_query:
            await safe_answer(
                update.callback_query,
                "Your Telegram account is not authorized. Ask an admin to check your numeric Telegram ID.",
                show_alert=True,
            )
        elif update.message:
            await safe_reply_text(
                update.message,
                "⛔ You are not authorized to use this bot.\nContact an administrator for access.",
                parse_mode=None,
            )
        logger.warning("[AUTH] Rejected Telegram interaction user_id=%s chat_type=%s", user.id, getattr(chat, "type", None))
        raise ApplicationHandlerStop


async def close_parts_text_middleware(update: Update, ctx) -> None:
    """Ensure replacement part numbers are never stolen by an older report draft."""
    if ctx.user_data.get("close_parts_case_id"):
        await recv_close_parts_text(update, ctx)
        raise ApplicationHandlerStop


# ── Startup / Shutdown ────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    try:
        webhook_info = await application.bot.get_webhook_info()
        if webhook_info.url:
            logger.warning("[TELEGRAM] Webhook active at startup: %s", webhook_info.url)
        await application.bot.delete_webhook(drop_pending_updates=False)
        webhook_info = await application.bot.get_webhook_info()
        if webhook_info.url:
            raise RuntimeError(f"Webhook is still active after deleteWebhook: {webhook_info.url}")
        logger.info("[TELEGRAM] Polling mode verified; webhook is disabled")
    except Exception:
        logger.exception("[TELEGRAM] Could not force polling mode before startup")
        raise

    await ensure_indexes()
    alert_h = application.bot_data.get("alert_handler")
    if alert_h:
        await asyncio.to_thread(alert_h.load_from_disk)

    base_commands = [
        ("start",       "Register with Kurtex Alert Bot"),
        ("shifts",      "Current shift roster"),
        ("mycases",     "Your active cases"),
        ("unassigned",  "Untouched unassigned cases"),
        ("done",        "Today's closed cases"),
        ("casehistory", "Full closed case history"),
        ("mystats",     "Your performance stats"),
        ("bug",         "Report a bot error or ask for help"),
        ("help",        "Commands and help"),
    ]
    manager_commands = base_commands + [
        ("alert",       "Broadcast an alert to all users"),
        ("report",      "Daily summary"),
        ("leaderboard", "Weekly top performers"),
        ("missed",      "Unhandled alerts today"),
        ("listusers",   "List all users and roles"),
        ("adduser",     "Add user by ID"),
        ("removeuser",  "Remove a user"),
        ("editrole",    "Change a user's role"),
    ]

    await application.bot.set_my_commands(base_commands)

    for uid_str, u in (await asyncio.to_thread(get_all_users)).items():
        if u["role"] in ("developer", "super_admin"):
            try:
                await application.bot.set_my_commands(
                    manager_commands,
                    scope=BotCommandScopeChat(chat_id=int(uid_str)),
                )
            except Exception as exc:
                logger.warning("Could not set commands for %s: %s", uid_str, exc)

    me = await application.bot.get_me()
    application.bot_data["bot_identity"] = {"id": me.id, "username": me.username or ""}
    await asyncio.to_thread(_write_bot_heartbeat, me.username or "")
    application.create_task(_bot_heartbeat_loop(application), name="kurtex-bot-heartbeat")
    logger.info("%s started as @%s", BOT_NAME, me.username)


async def post_shutdown(application: Application) -> None:
    stop_api_server()


# ── Commands ──────────────────────────────────────────────────────────────────

@with_typing
async def cmd_start(update: Update, ctx):
    user = update.effective_user
    stored = await asyncio.to_thread(get_user, user.id)
    if not stored:
        name = f"{user.first_name} {user.last_name or ''}".strip()
        await asyncio.to_thread(add_user, user.id, name, user.username or "", "agent")
        stored = await asyncio.to_thread(get_user, user.id)

    role = stored["role"] if stored else "agent"
    await safe_reply_text(
        update.message,
        f"👋 Welcome to <b>{esc_html(BOT_NAME)}!</b>\n\n"
        f"<i>{esc_html(BOT_TAGLINE)}</i>\n\n"
        f"You're registered as <b>{esc_html(role)}</b>.\n\n"
        "/mycases — Manage your active cases\n"
        "/shifts — See who is on duty\n"
        "/help — All commands",
    )


@with_typing
async def cmd_shifts(update: Update, ctx):
    state = await asyncio.to_thread(get_on_shift_state)
    shift_name = await asyncio.to_thread(get_current_shift_name)
    on_shift = state["on_duty_users"]
    all_shifts = state["shifts"]

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    lines = [f"🕒 <b>Current Shift:</b> {esc_html(shift_name)}\n"]

    if on_shift:
        lines.append("<b>On duty (receiving alerts):</b>")
        for a in on_shift:
            uname = f" (@{esc_html(a['username'])})" if a.get("username") else ""
            lines.append(f"  • {esc_html(a['name'])}{uname}")
    elif not state["has_configured_shifts"]:
        lines.append(
            "No shifts configured yet in Kurtex Desktop. All agents/admins receive alerts."
        )
    else:
        lines.append("No agents assigned to an active shift right now. Fallback notifies all admins.")

    if all_shifts:
        lines.append("\n<b>Configured Shifts:</b>")
        active_ids = {s["id"] for s in state["active_shifts"]}
        for s in all_shifts:
            status_icon = "🟢" if s["id"] in active_ids else ("⚪️" if s["enabled"] else "⏸")
            days_str = ", ".join(day_names[d] for d in s["days"] if 0 <= d < 7)
            lines.append(
                f"{status_icon} <b>{esc_html(s['name'])}</b> "
                f"({esc_html(s['start_time'])}–{esc_html(s['end_time'])}, {esc_html(s['timezone'])})\n"
                f"   Days: {esc_html(days_str)} · Assigned: {len(s.get('user_ids') or [])}"
            )

    await safe_reply_text(update.message, "\n".join(lines))


@with_typing
async def cmd_unassigned(update: Update, ctx):
    """Show untouched, still-unassigned cases with a fresh Assign button."""
    cases = await async_get_untouched_unassigned_cases()
    if not cases:
        await safe_reply_text(update.message, "No untouched unassigned cases.", parse_mode=None)
        return

    limit = 20
    shown = cases[:limit]
    await safe_reply_text(
        update.message,
        f"Unassigned untouched cases: {len(cases)}"
        + (f"\nShowing newest {limit}." if len(cases) > limit else ""),
        parse_mode=None,
    )

    for case in shown:
        case_id = case.get("id", "")
        opened_dt = chicago_timestamp(case.get("opened_at"))
        opened = opened_dt.strftime("%Y-%m-%d %H:%M") if opened_dt else ""
        text = (
            "🔔 <b>Unassigned Case</b>\n\n"
            f"<b>Group:</b> {esc_html(case.get('group_name'))}\n"
            f"<b>Driver:</b> {esc_html(case.get('driver_name'))}\n"
            f"<b>Issue:</b> {esc_html((case.get('description') or '—')[:300])}\n"
            f"<b>Opened:</b> {esc_html(opened or '—')} {esc_html(CENTRAL_TIMEZONE_LABEL)}"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Assign", callback_data=f"assign|{case_id}")
        ]])
        await safe_reply_text(update.message, text, reply_markup=kb)


@with_typing
async def cmd_help(update: Update, ctx):
    user = update.effective_user
    is_super = await asyncio.to_thread(_is_main_admin, user.id)
    is_dev = await asyncio.to_thread(has_role, user.id, "developer")
    words = "  ".join(TRIGGER_WORDS)

    text = (
        f"<b>{esc_html(BOT_NAME)}</b>\n<i>{esc_html(BOT_TAGLINE)}</i>\n\n"
        "📢 <b>Driver reporting</b> — post in driver group:\n"
        f"<code>{esc_html(words)}</code>\n\n"
        "<i>Example: #maintenance engine overheating, truck 42</i>\n\n"
        "<b>Agent commands:</b>\n"
        "/mycases — Active cases (paginated / compact list)\n"
        "/unassigned — Untouched unassigned cases\n"
        "/done — Today's closed cases\n"
        "/casehistory — Full history\n"
        "/mystats — Your stats\n"
        "/shifts — Shift roster\n"
        "/bug — Report a bot bug, error, or ask for help\n"
        "/myreports — Your submitted bot reports and conversation updates\n"
    )
    if is_super:
        text += (
            "\n<b>Admin commands:</b>\n"
            "/alert — Broadcast an alert to all users\n"
            "/report — Daily summary\n"
            "/leaderboard — Weekly top performers\n"
            "/missed — Unhandled alerts\n"
            "/listusers — All users and roles\n"
            "/adduser — Add user by ID and role\n"
            "/removeuser — Remove a user\n"
            "/editrole — Change a user's role\n"
            "\n💡 <b>Tip:</b> Forward any message from a user to add them quickly.\n"
        )
    if is_dev and not is_super:
        text += (
            "\n<b>Developer commands:</b>\n"
            "/alert — Broadcast an alert to all users\n"
            "/listusers — All users and roles\n"
            "/adduser — Add user by ID and role\n"
            "/removeuser — Remove a user\n"
            "/editrole — Change a user's role\n"
            "\n💡 <b>Tip:</b> Forward any message from a user to add them quickly.\n"
        )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🐞 Report Bot Issue / Help", callback_data="bug_start"),
    ]])
    await safe_reply_text(update.message, text, reply_markup=kb)


def _register_sigterm(application: Application):
    def _handle(signum, frame):
        logger.info("SIGTERM -- shutting down API worker and notifying admins")
        stop_api_server()

        async def _notify():
            try:
                for uid, udata in application.user_data.items():
                    if udata.get("report_case_id"):
                        try:
                            await safe_send_message(
                                application.bot,
                                uid,
                                "⚠️ <b>Bot is restarting.</b>\n\n"
                                "Your in-progress report is saved periodically. "
                                "When the bot returns, try continuing the report. "
                                "If the session has expired, open /mycases and press Report again.",
                            )
                        except Exception:
                            pass
            except Exception as exc:
                logger.warning("SIGTERM notify error: %s", exc)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_notify())
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _handle)


def main():
    instance_lock = _acquire_instance_lock()
    if instance_lock is None:
        raise SystemExit(1)

    alert_h = AlertHandler()

    bot_request = HTTPXRequest(
        connection_pool_size=64,
        connect_timeout=15.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=15.0,
    )
    updates_request = HTTPXRequest(
        connection_pool_size=4,
        connect_timeout=15.0,
        read_timeout=45.0,
        write_timeout=15.0,
        pool_timeout=15.0,
    )

    report_persistence = PicklePersistence(
        filepath=Path(config.DATA_DIR) / "telegram_report_sessions.pickle",
        store_data=PersistenceInput(
            user_data=True, chat_data=False, bot_data=False, callback_data=False,
        ),
        update_interval=30,
    )
    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .request(bot_request)
        .get_updates_request(updates_request)
        .persistence(report_persistence)
        .concurrent_updates(16)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    import bot_app.handlers.agent_handler as _ah
    _ah._bot_ref = app.bot

    async def error_handler(update, ctx):
        update_id = getattr(update, "update_id", "unknown") if update else "unknown"
        error = ctx.error

        if isinstance(error, Conflict) and "webhook" in str(error).lower():
            logger.error("[TELEGRAM] Polling/webhook conflict (update_id=%s): %s", update_id, error)
            try:
                await ctx.bot.delete_webhook(drop_pending_updates=False)
            except Exception as cleanup_error:
                logger.exception("[TELEGRAM] Failed to recover from webhook conflict: %s", cleanup_error)
            return

        if isinstance(error, BadRequest):
            logger.error(
                "Telegram rejected an update response (update_id=%s): %s",
                update_id, error,
                exc_info=(type(error), error, error.__traceback__),
            )
            return

        if isinstance(error, (NetworkError, TimedOut)):
            logger.warning("Telegram network interruption (update_id=%s): %s; polling will retry", update_id, error)
            return

        logger.error(
            "Unhandled update error (update_id=%s): %s",
            update_id, error,
            exc_info=(type(error), error, error.__traceback__),
        )

    app.add_error_handler(error_handler)
    app.bot_data["alert_handler"] = alert_h
    _register_sigterm(app)

    private = filters.ChatType.PRIVATE

    app.add_handler(TypeHandler(Update, runtime_update_middleware), group=-4)
    app.add_handler(TypeHandler(Update, callback_trace_middleware), group=-3)
    app.add_handler(TypeHandler(Update, auth_middleware), group=-2)
    app.add_handler(
        MessageHandler(private & filters.TEXT & ~filters.COMMAND, close_parts_text_middleware),
        group=-1,
    )

    # ── Core commands ──
    app.add_handler(CommandHandler("start",       cmd_start,       filters=private))
    app.add_handler(CommandHandler("shifts",      cmd_shifts,      filters=private))
    app.add_handler(CommandHandler("help",        cmd_help,        filters=private))
    app.add_handler(CommandHandler("done",        cmd_done,        filters=private))
    app.add_handler(CommandHandler("mycases",     cmd_mycases,     filters=private))
    app.add_handler(CommandHandler("unassigned",  cmd_unassigned,  filters=private))
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

    # ── Forward-to-add ──
    app.add_handler(MessageHandler(private & filters.FORWARDED, handle_forward))
    app.add_handler(CallbackQueryHandler(cb_addrole, pattern=r"^addrole\|"))

    # ── Conversation handlers (support/feedback, team /alert, report) + recovery ──
    app.add_handler(get_feedback_conversation())
    app.add_handler(get_ticket_conversation())
    app.add_handler(get_alert_broadcast_conversation())
    app.add_handler(get_report_conversation())

    async def recover_private_text(update, ctx):
        if ctx.user_data.get("close_parts_case_id"):
            await recv_close_parts_text(update, ctx)
        else:
            await recover_report_text(update, ctx)

    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, recover_private_text))
    app.add_handler(CallbackQueryHandler(cb_orphan_report_callback, pattern=r"^rpt_"))

    def _build_pattern(words):
        return "|".join(
            re.escape(w) if w.startswith("#") else r"\b" + re.escape(w) + r"\b"
            for w in words
        )

    _trigger_regex = f"(?i)({_build_pattern(TRIGGER_WORDS)})"
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & (filters.TEXT | filters.PHOTO)
        & (filters.Regex(_trigger_regex) | filters.CaptionRegex(_trigger_regex)),
        alert_h.handle,
    ))

    # ── Callbacks ──
    app.add_handler(CallbackQueryHandler(alert_h.handle_assignment,  pattern=r"^(assign|assignrpt|ignore)\|"))
    app.add_handler(CallbackQueryHandler(alert_h.handle_reassign,    pattern=r"^reassign_"))
    app.add_handler(CallbackQueryHandler(cb_mycases_page,            pattern=r"^(mycases_page\||mycases_noop$)"))
    app.add_handler(CallbackQueryHandler(cb_mycases_view,            pattern=r"^mycases_view\|"))
    app.add_handler(CallbackQueryHandler(cb_mycases_open,            pattern=r"^mycases_open\|"))
    app.add_handler(CallbackQueryHandler(cb_mycases_clear,           pattern=r"^mycases_clear$"))
    app.add_handler(CallbackQueryHandler(cb_mycases_next_case,       pattern=r"^mycases_next_case$"))
    app.add_handler(CallbackQueryHandler(cb_done_pick,               pattern=r"^done_pick\|"))
    app.add_handler(CallbackQueryHandler(cb_close_ask,               pattern=r"^close_ask\|"))
    app.add_handler(CallbackQueryHandler(cb_solve_confirm,           pattern=r"^solve_confirm\|"))
    app.add_handler(CallbackQueryHandler(cb_solve_cancel,            pattern=r"^solve_cancel\|"))
    app.add_handler(CallbackQueryHandler(cb_close_confirm,           pattern=r"^close_confirm\|"))
    app.add_handler(CallbackQueryHandler(cb_close_parts_yes,         pattern=r"^close_parts_yes\|"))
    app.add_handler(CallbackQueryHandler(cb_close_parts_no,          pattern=r"^close_parts_no\|"))
    app.add_handler(CallbackQueryHandler(cb_close_parts_skip,        pattern=r"^close_parts_skip\|"))
    app.add_handler(CallbackQueryHandler(cb_close_parts_cancel,      pattern=r"^close_parts_cancel\|"))
    app.add_handler(CallbackQueryHandler(cb_close_cancel,            pattern=r"^close_cancel\|"))
    app.add_handler(CallbackQueryHandler(cb_delete_confirm,          pattern=r"^delete_confirm\|"))
    app.add_handler(CallbackQueryHandler(cb_delete_do,               pattern=r"^delete_do\|"))
    app.add_handler(CallbackQueryHandler(cb_delete_keep,             pattern=r"^delete_keep\|"))
    app.add_handler(CallbackQueryHandler(cb_histpage,                pattern=r"^histpage\|"))
    app.add_handler(CallbackQueryHandler(cb_hist_delete_chat,        pattern=r"^hist_delete_chat$"))

    register_jobs(app)

    start_api_thread()
    logger.info("Starting %s...", BOT_NAME)
    app.run_polling(
        drop_pending_updates=False,
        timeout=20,
        bootstrap_retries=-1,
    )


if __name__ == "__main__":
    main()
