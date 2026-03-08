"""
handlers/agent_handler.py

/mycases      - active cases with Report + Close buttons
/casehistory  - closed cases (paginated)
"""

import asyncio
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler,
    CommandHandler, MessageHandler, filters
)
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

CASES_PER_PAGE    = 5
AWAITING_SOLUTION = 1
AWAITING_CLOSE_REASON = 2


def _fmt_dt(iso):
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso).astimezone()
        return dt.strftime("%b %d %H:%M")
    except Exception:
        return iso[:16]


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
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Report", callback_data=f"solve|{case_id}"),
        InlineKeyboardButton("✅ Close",  callback_data=f"close_ask|{case_id}"),
    ]])


async def _delete_after(bot, chat_id, message_id, seconds):
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass


# ── /mycases ──────────────────────────────────────────────────────────────────

async def cmd_mycases(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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


# ── /casehistory ──────────────────────────────────────────────────────────────

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

    ctx.user_data["history_msg_ids"] = []
    await _send_history_page(update.message, user.id, page=0, cases=closed_only, ctx=ctx)


async def _send_history_page(target, agent_id, page, cases=None, ctx=None):
    if cases is None:
        all_cases = list(reversed(get_all_cases_for_agent(agent_id)))
        cases     = [c for c in all_cases if c["status"] == "done"]

    total        = len(cases)
    start        = page * CASES_PER_PAGE
    end          = min(start + CASES_PER_PAGE, total)
    batch        = cases[start:end]
    is_last_page = end >= total
    chat_id      = agent_id

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
    query   = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    msg_ids = ctx.user_data.pop("history_msg_ids", [])
    msg_ids.append(query.message.message_id)
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except TelegramError:
            pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_one_at_a_time(ctx, case_id):
    """Returns True if blocked (already working on a different case)."""
    existing = ctx.user_data.get("solving_case_id")
    if existing and existing != case_id:
        existing_case = get_case(existing)
        if existing_case and existing_case["status"] == "assigned":
            return True
    return False


async def _show_remaining(bot, agent_id):
    await asyncio.sleep(2)
    all_cases   = get_all_cases_for_agent(agent_id)
    active_only = [c for c in all_cases if c["status"] == "assigned"]
    if active_only:
        for c in active_only:
            try:
                await bot.send_message(
                    agent_id, _active_case_text(c),
                    parse_mode="Markdown",
                    reply_markup=_active_case_keyboard(c["id"])
                )
            except TelegramError:
                pass
    else:
        try:
            await bot.send_message(agent_id, "✅ No more active cases. You are free!")
        except TelegramError:
            pass


# ── Report (Solve) flow ───────────────────────────────────────────────────────

async def cb_solve_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    case_id = query.data.split("|")[1]
    case    = get_case(case_id)

    if not case or case["status"] != "assigned":
        await query.edit_message_text("This case is no longer active.", reply_markup=None)
        return ConversationHandler.END

    if _check_one_at_a_time(ctx, case_id):
        await query.answer("Finish your current case first.", show_alert=True)
        return ConversationHandler.END

    ctx.user_data.pop("pending_solution", None)
    ctx.user_data["solving_case_id"] = case_id

    await query.edit_message_text(
        f"📋 Reporting case:\n\n"
        f"Driver: {case['driver_name']} — {case['group_name']}\n"
        f"Issue: {(case.get('description') or '')[:80]}\n\n"
        "Type your resolution note (or /cancel):"
    )
    return AWAITING_SOLUTION


async def cb_solve_receive_solution(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    solution = update.message.text.strip()

    if not solution or len(solution) < 3:
        await update.message.reply_text(
            "⚠️ Please describe what was done (min 3 characters):"
        )
        return AWAITING_SOLUTION

    case_id = ctx.user_data.get("solving_case_id")
    case    = get_case(case_id) if case_id else None

    if not case:
        await update.message.reply_text("Something went wrong. Try /mycases again.")
        ctx.user_data.pop("solving_case_id", None)
        return ConversationHandler.END

    ctx.user_data["pending_solution"] = solution

    await update.message.reply_text(
        f"Confirm closing this case?\n\n"
        f"Group: {case['group_name']}\n"
        f"Driver: {case['driver_name']}\n"
        f"Note: {solution}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes, close it", callback_data=f"solve_confirm|{case_id}"),
            InlineKeyboardButton("❌ Cancel",        callback_data=f"solve_cancel|{case_id}"),
        ]])
    )
    return ConversationHandler.END


async def cb_solve_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    case_id  = query.data.split("|")[1]
    solution = ctx.user_data.pop("pending_solution", None)
    ctx.user_data.pop("solving_case_id", None)

    if not solution:
        await query.edit_message_text(
            "⚠️ No resolution note found. Use /mycases and try again.",
            reply_markup=None
        )
        return

    close_case(case_id, notes=solution)
    case = get_case(case_id)

    await query.edit_message_text(
        f"✅ Case closed!\n\n"
        f"Group: {case['group_name'] if case else '—'}\n"
        f"Driver: {case['driver_name'] if case else '—'}\n"
        f"Note: {solution}",
        reply_markup=None
    )

    asyncio.create_task(_show_remaining(query.bot, update.effective_user.id))


async def cb_solve_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    ctx.user_data.pop("pending_solution", None)
    ctx.user_data.pop("solving_case_id", None)
    case_id = query.data.split("|")[1]
    case    = get_case(case_id)

    if case and case["status"] == "assigned":
        await query.edit_message_text(
            _active_case_text(case),
            parse_mode="Markdown",
            reply_markup=_active_case_keyboard(case["id"])
        )
    else:
        await query.edit_message_text("Case not found.", reply_markup=None)


async def cmd_solve_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("solving_case_id", None)
    ctx.user_data.pop("pending_solution", None)
    ctx.user_data.pop("pending_close_reason", None)
    await update.message.reply_text("Cancelled. Use /mycases to see your cases.")
    return ConversationHandler.END


# ── Close flow ────────────────────────────────────────────────────────────────

async def cb_close_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ask for a close reason before closing."""
    query   = update.callback_query
    await query.answer()
    case_id = query.data.split("|")[1]
    case    = get_case(case_id)

    if not case or case["status"] != "assigned":
        await query.edit_message_text("This case is no longer active.", reply_markup=None)
        return ConversationHandler.END

    if _check_one_at_a_time(ctx, case_id):
        await query.answer("Finish your current case first.", show_alert=True)
        return ConversationHandler.END

    ctx.user_data.pop("pending_close_reason", None)
    ctx.user_data["solving_case_id"] = case_id

    await query.edit_message_text(
        f"✅ Closing case:\n\n"
        f"Driver: {case['driver_name']} — {case['group_name']}\n"
        f"Issue: {(case.get('description') or '')[:80]}\n\n"
        "How was this resolved? (or /cancel):"
    )
    return AWAITING_CLOSE_REASON


async def cb_close_receive_reason(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reason  = update.message.text.strip()

    if not reason or len(reason) < 3:
        await update.message.reply_text(
            "⚠️ Please describe how it was resolved (min 3 characters):"
        )
        return AWAITING_CLOSE_REASON

    case_id = ctx.user_data.get("solving_case_id")
    case    = get_case(case_id) if case_id else None

    if not case:
        await update.message.reply_text("Something went wrong. Try /mycases again.")
        ctx.user_data.pop("solving_case_id", None)
        return ConversationHandler.END

    # Close immediately after reason is typed — no extra confirm step
    close_case(case_id, notes=reason)
    ctx.user_data.pop("solving_case_id", None)
    ctx.user_data.pop("pending_close_reason", None)

    await update.message.reply_text(
        f"✅ Case closed!\n\n"
        f"Group: {case['group_name']}\n"
        f"Driver: {case['driver_name']}\n"
        f"Reason: {reason}"
    )

    asyncio.create_task(_show_remaining(ctx.bot, update.effective_user.id))
    return ConversationHandler.END


# ── /done ─────────────────────────────────────────────────────────────────────

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_admin(user.id):
        await update.message.reply_text("Not authorized.")
        return

    today_cases  = get_cases_for_agent_today(user.id)
    closed_today = [c for c in today_cases if c["status"] == "done"]

    if not closed_today:
        await update.message.reply_text("No cases closed today yet.")
        return

    lines = [f"Today's closed cases: {len(closed_today)}\n"]
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


# Keep for backward compat
async def cb_delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    case_id = query.data.split("|")[1]
    query.data = f"close_ask|{case_id}"
    return await cb_close_ask(update, ctx)


async def cb_delete_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Cancelled.", reply_markup=None)


async def cb_delete_keep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    case_id = query.data.split("|")[1]
    case    = get_case(case_id)
    if case and case["status"] == "assigned":
        await query.edit_message_text(
            _active_case_text(case),
            parse_mode="Markdown",
            reply_markup=_active_case_keyboard(case["id"])
        )
    else:
        await query.edit_message_text("Case not found.", reply_markup=None)


# ── Conversation handlers ─────────────────────────────────────────────────────

def get_solve_conversation():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_solve_start, pattern=r'^solve\|'),
            CallbackQueryHandler(cb_done_pick,   pattern=r'^done_pick\|'),
            CallbackQueryHandler(cb_close_ask,   pattern=r'^close_ask\|'),
        ],
        states={
            AWAITING_SOLUTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cb_solve_receive_solution)
            ],
            AWAITING_CLOSE_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cb_close_receive_reason)
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_solve_cancel)],
        per_message=False,
        allow_reentry=True,
    )
