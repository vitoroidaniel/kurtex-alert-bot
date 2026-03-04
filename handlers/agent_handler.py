"""
handlers/agent_handler.py

/mycases      - active cases with Solve + Delete buttons
/casehistory  - closed cases only (paginated)
"""

import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from telegram.error import TelegramError

from storage.case_store import (
    get_active_case_for_agent,
    get_cases_for_agent_today,
    get_all_cases_for_agent,
    close_case,
    get_case,
)
from shifts import ADMINS

logger = logging.getLogger(__name__)

CASES_PER_PAGE = 5
AWAITING_SOLUTION = 1


def _fmt_dt(iso):
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso).astimezone()
        return dt.strftime("%b %d %H:%M")
    except Exception:
        return iso[:16]


def _fmt_secs(secs):
    if secs is None:
        return "—"
    m, s = divmod(secs, 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _is_admin(user_id):
    return user_id in ADMINS


def _active_case_text(case):
    return (
        f"📋 *Active Case*\n\n"
        f"📌 *Group:* {case['group_name']}\n"
        f"👤 *Driver:* {case['driver_name']}\n"
        f"📝 *Issue:* {(case.get('description') or '—')[:200]}"
    )


def _active_case_keyboard(case_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Report", callback_data=f"solve|{case_id}"),
            InlineKeyboardButton("✅ Close",  callback_data=f"delete_confirm|{case_id}"),
        ],
    ])


# ── /mycases — active cases ───────────────────────────────────────────────────

async def cmd_mycases(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Active cases with Close button."""
    user = update.effective_user
    if not _is_admin(user.id):
        await update.message.reply_text("Not authorized.")
        return

    all_cases   = get_all_cases_for_agent(user.id)
    active_only = [c for c in all_cases if c["status"] == "assigned"]

    if not active_only:
        await update.message.reply_text("No active cases. You are free!")
        return

    for case in active_only:
        await update.message.reply_text(
            _active_case_text(case),
            parse_mode="Markdown",
            reply_markup=_active_case_keyboard(case["id"])
        )


# ── /casehistory — closed cases ───────────────────────────────────────────────

async def cmd_casehistory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_admin(user.id):
        await update.message.reply_text("Not authorized.")
        return

    all_cases   = list(reversed(get_all_cases_for_agent(user.id)))
    closed_only = [c for c in all_cases if c["status"] == "done"]

    if not closed_only:
        await update.message.reply_text("No closed cases yet.")
        return

    # Track all sent message IDs so we can delete them later
    ctx.user_data["history_msg_ids"] = []
    await _send_history_page(update.message, user.id, page=0, cases=closed_only, ctx=ctx)


async def _send_history_page(target, agent_id, page, cases=None, ctx=None):
    if cases is None:
        all_cases = list(reversed(get_all_cases_for_agent(agent_id)))
        cases     = [c for c in all_cases if c["status"] == "done"]

    total      = len(cases)
    start      = page * CASES_PER_PAGE
    end        = min(start + CASES_PER_PAGE, total)
    batch      = cases[start:end]
    is_last_page = end >= total
    chat_id    = agent_id

    async def _send(text, reply_markup=None):
        if hasattr(target, "reply_text"):
            sent = await target.reply_text(text, reply_markup=reply_markup)
        else:
            sent = await target.get_bot().send_message(chat_id, text, reply_markup=reply_markup)
        if ctx and "history_msg_ids" in ctx.user_data:
            ctx.user_data["history_msg_ids"].append(sent.message_id)
        return sent

    for i, case in enumerate(batch):
        num  = start + i + 1
        text = (
            f"Case {num}\n\n"
            f"Group: {case['group_name']}\n"
            f"Driver: {case['driver_name']}\n"
            f"Issue: {(case.get('description') or '')[:80]}\n"
            f"Assigned: {_fmt_dt(case.get('assigned_at'))}\n"
            f"Closed: {_fmt_dt(case.get('closed_at'))}"
            + (f"\nNote: {case['notes']}" if case.get("notes") else "")
        )

        nav = []
        if i == len(batch) - 1:
            if page > 0:
                nav.append(InlineKeyboardButton("Prev", callback_data=f"histpage|{page - 1}"))
            if end < total:
                nav.append(InlineKeyboardButton("Next", callback_data=f"histpage|{page + 1}"))

        kb = InlineKeyboardMarkup([nav]) if nav else None
        await _send(text, reply_markup=kb)

    # Footer + delete button only on last page
    total_pages = ((total - 1) // CASES_PER_PAGE) + 1
    footer      = f"Page {page + 1} of {total_pages}  ({total} closed)"

    if is_last_page:
        delete_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Delete history from chat", callback_data="hist_delete_chat")
        ]])
        await _send(footer, reply_markup=delete_kb)
    else:
        await _send(footer)


async def cb_histpage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    page     = int(query.data.split("|")[1])
    agent_id = query.from_user.id
    all_cases   = list(reversed(get_all_cases_for_agent(agent_id)))
    closed_only = [c for c in all_cases if c["status"] == "done"]
    if "history_msg_ids" not in ctx.user_data:
        ctx.user_data["history_msg_ids"] = []
    await _send_history_page(query, agent_id, page, cases=closed_only, ctx=ctx)


async def cb_hist_delete_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Delete all history messages from chat."""
    query = update.callback_query
    await query.answer()
    bot     = ctx.bot
    chat_id = query.from_user.id
    msg_ids = ctx.user_data.pop("history_msg_ids", [])

    # Also delete the message with the delete button itself
    msg_ids.append(query.message.message_id)

    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except TelegramError:
            pass


# ── Solve flow ────────────────────────────────────────────────────────────────

async def cb_solve_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    case_id = query.data.split("|")[1]
    case    = get_case(case_id)

    if not case or case["status"] != "assigned":
        await query.edit_message_text("This case is no longer active.", reply_markup=None)
        return ConversationHandler.END

    # Clear any previous solve state to avoid cross-case confusion
    ctx.user_data.pop("pending_solution", None)
    ctx.user_data["solving_case_id"] = case_id

    await query.edit_message_text(
        f"Solving: {case['driver_name']} — {case['group_name']}\n"
        f"Issue: {(case.get('description') or '')[:80]}\n\n"
        "Type your resolution note (or /cancel):"
    )
    return AWAITING_SOLUTION


async def cb_solve_receive_solution(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    solution = update.message.text.strip()

    # Resolution note is mandatory
    if not solution or len(solution) < 3:
        await update.message.reply_text(
            "A resolution note is required to close a case.\n\n"
            "Please describe what was done to resolve the issue:"
        )
        return AWAITING_SOLUTION

    case_id  = ctx.user_data.get("solving_case_id")
    case     = get_case(case_id) if case_id else None

    if not case:
        await update.message.reply_text("Something went wrong. Try /mycases again.")
        return ConversationHandler.END

    ctx.user_data["pending_solution"] = solution

    await update.message.reply_text(
        f"Confirm solution?\n\n"
        f"Group: {case['group_name']}\n"
        f"Driver: {case['driver_name']}\n"
        f"Solution: {solution}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes, close it", callback_data=f"solve_confirm|{case_id}"),
            InlineKeyboardButton("Cancel",        callback_data=f"solve_cancel|{case_id}"),
        ]])
    )
    return ConversationHandler.END


async def cb_solve_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    case_id  = query.data.split("|")[1]
    solution = ctx.user_data.pop("pending_solution", None)
    ctx.user_data.pop("solving_case_id", None)

    close_case(case_id, notes=solution)
    case = get_case(case_id)

    # Show confirmation then auto-delete after 5 seconds
    await query.edit_message_text(
        f"Case solved!\n\n"
        f"Group: {case['group_name'] if case else '—'}\n"
        f"Driver: {case['driver_name'] if case else '—'}\n"
        f"Solution: {solution or '—'}",
        reply_markup=None
    )
    asyncio.create_task(_delete_after(query.bot, query.message.chat_id, query.message.message_id, 5))

    # After delay, show remaining active cases
    async def _show_remaining():
        await asyncio.sleep(5)
        agent_id    = update.effective_user.id
        all_cases   = get_all_cases_for_agent(agent_id)
        active_only = [c for c in all_cases if c["status"] == "assigned"]
        if active_only:
            for c in active_only:
                try:
                    await query.bot.send_message(
                        agent_id,
                        _active_case_text(c),
                        reply_markup=_active_case_keyboard(c["id"])
                    )
                except TelegramError:
                    pass
        else:
            try:
                await query.bot.send_message(agent_id, "No more active cases. You are free!")
            except TelegramError:
                pass

    asyncio.create_task(_show_remaining())


async def cb_solve_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    ctx.user_data.pop("pending_solution", None)
    case_id = query.data.split("|")[1]
    case    = get_case(case_id)

    if case and case["status"] == "assigned":
        await query.edit_message_text(
            _active_case_text(case),
            reply_markup=_active_case_keyboard(case["id"])
        )
    else:
        await query.edit_message_text("Case not found.", reply_markup=None)


async def cmd_solve_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("solving_case_id", None)
    ctx.user_data.pop("pending_solution", None)
    await update.message.reply_text("Cancelled. Use /mycases to see your cases.")
    return ConversationHandler.END


# ── Delete flow ───────────────────────────────────────────────────────────────

async def cb_delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    case_id = query.data.split("|")[1]
    case    = get_case(case_id)

    if not case:
        await query.edit_message_text("Case not found.", reply_markup=None)
        return

    await query.edit_message_text(
        f"Are you sure you want to delete this case?\n\n"
        f"Group: {case['group_name']}\n"
        f"Driver: {case['driver_name']}\n"
        f"Issue: {(case.get('description') or '')[:80]}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes, delete", callback_data=f"delete_do|{case_id}"),
            InlineKeyboardButton("No, keep it", callback_data=f"delete_keep|{case_id}"),
        ]])
    )


async def cb_delete_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sent = await query.edit_message_text("Case deleted.", reply_markup=None)

    # Auto-delete after 5 seconds
    asyncio.create_task(_delete_after(query.bot, query.message.chat_id, query.message.message_id, 5))


async def cb_delete_keep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    case_id = query.data.split("|")[1]
    case    = get_case(case_id)

    if case and case["status"] == "assigned":
        await query.edit_message_text(
            _active_case_text(case),
            reply_markup=_active_case_keyboard(case["id"])
        )
    else:
        await query.edit_message_text("Case not found.", reply_markup=None)


# ── /done (via command) ───────────────────────────────────────────────────────

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Today's closed cases — summary."""
    user = update.effective_user
    if not _is_admin(user.id):
        await update.message.reply_text("Not authorized.")
        return

    today_cases  = get_cases_for_agent_today(user.id)
    closed_today = [c for c in today_cases if c["status"] == "done"]

    if not closed_today:
        await update.message.reply_text("No cases closed today yet.")
        return

    lines = [f"Today closed cases: {len(closed_today)}\n"]
    for i, c in enumerate(closed_today, 1):
        note = f"\n   Note: {c['notes']}" if c.get("notes") else ""
        lines.append(
            f"{i}. {c['driver_name']} — {c['group_name']}\n"
            f"   Closed: {_fmt_dt(c.get('closed_at'))}"
            f"{note}"
        )

    await update.message.reply_text("\n".join(lines))


async def cb_done_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    case_id = query.data.split("|")[1]
    case    = get_case(case_id)

    if not case:
        await query.edit_message_text("Case not found.", reply_markup=None)
        return ConversationHandler.END

    ctx.user_data["solving_case_id"] = case_id
    await query.edit_message_text(
        f"Closing case:\n\n"
        f"Group: {case['group_name']}\n"
        f"Driver: {case['driver_name']}\n\n"
        "Type your solution (or /cancel to go back):",
        reply_markup=None
    )
    return AWAITING_SOLUTION


# ── Helper: delete message after N seconds ────────────────────────────────────

async def _delete_after(bot, chat_id, message_id, seconds):
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass


# ── Conversation handler (exported) ──────────────────────────────────────────

def get_solve_conversation():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_solve_start,  pattern=r'^solve\|'),
            CallbackQueryHandler(cb_done_pick,    pattern=r'^done_pick\|'),
        ],
        states={
            AWAITING_SOLUTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cb_solve_receive_solution)
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_solve_cancel)],
        per_message=False,
    )
