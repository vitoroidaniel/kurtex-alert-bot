"""handlers/agent_handler.py -- Fast, paginated active cases and safe solve/close workflows."""

from __future__ import annotations

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

from backend.core.app_time import chicago_date_str, chicago_timestamp, today_str, week_start_str
from backend.storage.case_store import (
    async_close_case as close_case,
    async_count_active_cases_for_agent,
    async_get_active_case_for_agent as get_active_case_for_agent,
    async_get_active_cases_for_agent,
    async_get_agent_cases_page_data,
    async_get_all_cases_for_agent as get_all_cases_for_agent,
    async_get_case as get_case,
    async_get_cases_for_agent_today as get_cases_for_agent_today,
)
from backend.storage.user_store import has_role
from bot_app.telegram_utils import (
    concurrent_ack,
    delete_messages_batch,
    esc_html,
    format_age_short,
    safe_answer,
    safe_edit_message_text,
    safe_reply_text,
    safe_send_message,
)

logger = logging.getLogger(__name__)
CASES_PER_PAGE = 5
COMPACT_CASES_PER_PAGE = 10
_bot_ref = None  # kept for backward compatibility


def _is_admin(user_id: int) -> bool:
    return has_role(user_id, "agent", "super_admin", "developer")


def _fmt_dt(iso) -> str:
    if not iso:
        return "—"
    try:
        dt = chicago_timestamp(iso)
        return dt.strftime("%b %d %H:%M") if dt else str(iso)[:16]
    except Exception:
        return str(iso)[:16]


def _active_case_text(case: dict, index: int | None = None, total: int | None = None) -> str:
    status = case.get("status", "assigned")
    badge = "📋 <b>Reported Case</b>" if status == "reported" else "⏳ <b>Non-Reported Case</b>"
    if index is not None and total is not None:
        badge = f"{badge} ({index}/{total})"
    opened = _fmt_dt(case.get("opened_at"))
    age = format_age_short(case.get("opened_at"))
    age_part = f" ({esc_html(age)})" if age else ""
    issue = (case.get("description") or "—")[:200]
    return (
        f"{badge}\n\n"
        f"⏱ <b>Opened:</b> {esc_html(opened)}{age_part}\n"
        f"📌 <b>Group:</b> {esc_html(case.get('group_name'))}\n"
        f"👤 <b>Reported by:</b> {esc_html(case.get('driver_name'))}\n"
        f"📝 <b>Issue:</b> {esc_html(issue)}"
    )


def _active_case_keyboard(case_id: str, status: str = "assigned") -> InlineKeyboardMarkup:
    if status == "reported":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Solve",    callback_data=f"close_ask|{case_id}"),
            InlineKeyboardButton("🔁 Reassign", callback_data=f"reassign_{case_id}"),
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Solve",    callback_data=f"close_ask|{case_id}"),
        InlineKeyboardButton("📋 Report",   callback_data=f"solve|{case_id}"),
        InlineKeyboardButton("🔁 Reassign", callback_data=f"reassign_{case_id}"),
    ]])


def _mycases_nav_keyboard(
    flt: str,
    page: int,
    total_pages: int,
    counts: dict,
    *,
    view: str = "cards",
    top_rows: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = list(top_rows or [])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("← Prev", callback_data=f"mycases_page|{flt}|{page - 1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="mycases_noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next →", callback_data=f"mycases_page|{flt}|{page + 1}"))
    if nav:
        rows.append(nav)

    def _fbtn(key: str, label: str) -> InlineKeyboardButton:
        prefix = "• " if flt == key else ""
        return InlineKeyboardButton(f"{prefix}{label}", callback_data=f"mycases_page|{key}|0")

    rows.append([
        _fbtn("all", f"All ({counts['total']})"),
        _fbtn("assigned", f"Non-Rep ({counts['assigned']})"),
        _fbtn("reported", f"Reported ({counts['reported']})"),
    ])
    toggle_btn = (
        InlineKeyboardButton("🗂 Card View", callback_data=f"mycases_view|cards|{flt}|0")
        if view == "compact"
        else InlineKeyboardButton("📄 Compact List", callback_data=f"mycases_view|compact|{flt}|0")
    )
    rows.append([
        toggle_btn,
        InlineKeyboardButton("🗑 Clear from chat", callback_data="mycases_clear"),
    ])
    return InlineKeyboardMarkup(rows)


def _compact_mycases_payload(
    flt: str,
    page: int,
    total_pages: int,
    counts: dict,
    batch: list[dict],
) -> tuple[str, InlineKeyboardMarkup]:
    label_map = {"all": "All Active", "assigned": "Non-Reported", "reported": "Reported"}
    filtered_total = counts["total"] if flt == "all" else counts[flt]
    offset = page * COMPACT_CASES_PER_PAGE
    lines = [
        f"📋 <b>Your {label_map[flt]} Cases (Compact)</b> — Page {page + 1}/{total_pages}",
        f"Total: <b>{counts['total']}</b> ({counts['assigned']} non-reported · {counts['reported']} reported) · Pick a # to open:",
        "",
    ]
    pick_buttons: list[InlineKeyboardButton] = []
    for pos, case in enumerate(batch, start=1):
        global_num = offset + pos
        icon = "📋" if case.get("status") == "reported" else "⏳"
        age = format_age_short(case.get("opened_at"))
        age_part = f" · {esc_html(age)}" if age else ""
        issue = (case.get("description") or "—").replace("\n", " ")[:48]
        lines.append(
            f"<b>{global_num}.</b> {icon} <b>{esc_html(case.get('group_name'))}</b> — "
            f"{esc_html(case.get('driver_name'))}{age_part}\n"
            f"     <i>{esc_html(issue)}</i>"
        )
        pick_buttons.append(InlineKeyboardButton(str(global_num), callback_data=f"mycases_open|{case['id']}"))
    lines.append(f"\nShowing {offset + 1}–{offset + len(batch)} of {filtered_total}")

    top_rows = [pick_buttons[i:i + 5] for i in range(0, len(pick_buttons), 5)]
    kb = _mycases_nav_keyboard(flt, page, total_pages, counts, view="compact", top_rows=top_rows)
    return "\n".join(lines), kb


async def _send_mycases_page(
    bot,
    chat_id: int,
    ctx,
    flt: str = "all",
    page: int = 0,
    *,
    edit_query=None,
) -> None:
    if flt not in ("all", "assigned", "reported"):
        flt = "all"

    view = "compact" if ctx.user_data.get("mycases_view") == "compact" else "cards"
    per_page = COMPACT_CASES_PER_PAGE if view == "compact" else CASES_PER_PAGE

    old_ids = list(ctx.user_data.pop("mycases_msg_ids", []) or [])
    edit_msg_id = getattr(getattr(edit_query, "message", None), "message_id", None)
    can_edit_compact = bool(view == "compact" and edit_query is not None and edit_msg_id is not None)
    if can_edit_compact:
        delete_ids = [mid for mid in old_ids if mid != edit_msg_id]
    else:
        delete_ids = old_ids

    # Delete prior page messages concurrently with DB fetch + sending the new page.
    del_task = asyncio.create_task(delete_messages_batch(bot, chat_id, delete_ids)) if delete_ids else None
    try:
        counts, flt, total_pages, page, batch = await async_get_agent_cases_page_data(
            chat_id, status_filter=flt, page=page, per_page=per_page
        )
        if counts["total"] == 0:
            if can_edit_compact:
                await safe_edit_message_text(edit_query, "✅ No active cases. You are free!", parse_mode=None, reply_markup=None)
            else:
                await safe_send_message(bot, chat_id, "✅ No active cases. You are free!", parse_mode=None)
            return

        if view == "compact":
            text, kb = _compact_mycases_payload(flt, page, total_pages, counts, batch)
            if can_edit_compact:
                await safe_edit_message_text(edit_query, text, reply_markup=kb)
                ctx.user_data["mycases_msg_ids"] = [edit_msg_id]
            else:
                msg = await safe_send_message(bot, chat_id, text, reply_markup=kb)
                ctx.user_data["mycases_msg_ids"] = [msg.message_id] if msg else []
            return

        filtered_total = counts["total"] if flt == "all" else counts[flt]
        offset = page * CASES_PER_PAGE
        sent_ids: list[int] = []
        label_map = {"all": "All Active", "assigned": "Non-Reported", "reported": "Reported"}
        header = (
            f"📋 <b>Your {label_map[flt]} Cases</b> — Page {page + 1}/{total_pages}\n"
            f"Total: <b>{counts['total']}</b> ({counts['assigned']} non-reported · {counts['reported']} reported)"
        )
        hdr_msg = await safe_send_message(bot, chat_id, header)
        if hdr_msg:
            sent_ids.append(hdr_msg.message_id)

        for idx, case in enumerate(batch, start=offset + 1):
            msg = await safe_send_message(
                bot,
                chat_id,
                _active_case_text(case, index=idx, total=filtered_total),
                reply_markup=_active_case_keyboard(case["id"], case.get("status", "assigned")),
            )
            if msg:
                sent_ids.append(msg.message_id)

        footer = f"Showing {offset + 1}–{offset + len(batch)} of {filtered_total} · Filter or browse:"
        ftr_msg = await safe_send_message(
            bot,
            chat_id,
            footer,
            parse_mode=None,
            reply_markup=_mycases_nav_keyboard(flt, page, total_pages, counts, view="cards"),
        )
        if ftr_msg:
            sent_ids.append(ftr_msg.message_id)

        ctx.user_data["mycases_msg_ids"] = sent_ids[-30:]
    finally:
        if del_task is not None:
            await del_task


# ── /mycases ──────────────────────────────────────────────────────────────────

async def cmd_mycases(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_admin(user.id):
        await safe_reply_text(update.message, "Not authorized.", parse_mode=None)
        return
    bot = ctx.bot if ctx and getattr(ctx, "bot", None) else update.get_bot()
    await _send_mycases_page(bot, user.id, ctx, flt="all", page=0)


async def cb_mycases_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        if query.data == "mycases_noop":
            return
        parts = (query.data or "").split("|")
        flt = parts[1] if len(parts) > 1 else "all"
        try:
            page = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            page = 0
        bot = ctx.bot if ctx and getattr(ctx, "bot", None) else query.get_bot()
        edit_q = query if ctx.user_data.get("mycases_view") == "compact" else None
        await _send_mycases_page(bot, query.from_user.id, ctx, flt=flt, page=page, edit_query=edit_q)


async def cb_mycases_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Toggle between 5-card view and 1-message Compact List (10 cases/page)."""
    query = update.callback_query
    async with concurrent_ack(query):
        parts = (query.data or "").split("|")
        view = "compact" if (len(parts) > 1 and parts[1] == "compact") else "cards"
        flt = parts[2] if len(parts) > 2 else "all"
        try:
            page = int(parts[3]) if len(parts) > 3 else 0
        except ValueError:
            page = 0
        ctx.user_data["mycases_view"] = view
        bot = ctx.bot if ctx and getattr(ctx, "bot", None) else query.get_bot()
        await _send_mycases_page(bot, query.from_user.id, ctx, flt=flt, page=page)


async def cb_mycases_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Open a single interactive case card selected from Compact List."""
    query = update.callback_query
    async with concurrent_ack(query):
        case_id = (query.data or "").split("|", 1)[1] if "|" in (query.data or "") else ""
        case = await get_case(case_id)
        if not case or case.get("status") not in ("assigned", "reported"):
            if getattr(query, "message", None):
                await safe_reply_text(query.message, "This case is no longer active.", parse_mode=None)
            return
        bot = ctx.bot if ctx and getattr(ctx, "bot", None) else query.get_bot()
        sent = await safe_send_message(
            bot,
            query.from_user.id,
            _active_case_text(case),
            reply_markup=_active_case_keyboard(case["id"], case.get("status", "assigned")),
        )
        if sent:
            ids = ctx.user_data.setdefault("mycases_msg_ids", [])
            ids.append(sent.message_id)
            if len(ids) > 30:
                del ids[:-30]


async def cb_mycases_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        chat_id = query.from_user.id
        msg_ids = list(ctx.user_data.pop("mycases_msg_ids", []) or [])
        if query.message and query.message.message_id not in msg_ids:
            msg_ids.append(query.message.message_id)
        bot = ctx.bot if ctx and getattr(ctx, "bot", None) else query.get_bot()
        await delete_messages_batch(bot, chat_id, msg_ids)


async def cb_mycases_next_case(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Send the single next active case (prioritizing non-reported) without flooding."""
    query = update.callback_query
    async with concurrent_ack(query):
        agent_id = query.from_user.id
        counts, _, _, _, batch = await async_get_agent_cases_page_data(
            agent_id, status_filter="assigned", page=0, per_page=1
        )
        if counts["total"] == 0 or not batch:
            await safe_edit_message_text(query, "✅ No more active cases. You are free!", parse_mode=None, reply_markup=None)
            return

        case = batch[0]
        bot = ctx.bot if ctx and getattr(ctx, "bot", None) else query.get_bot()
        await safe_send_message(
            bot,
            agent_id,
            _active_case_text(case, index=1, total=counts["total"]),
            reply_markup=_active_case_keyboard(case["id"], case.get("status", "assigned")),
        )


# ── /mystats ──────────────────────────────────────────────────────────────────

async def cmd_mystats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_admin(user.id):
        await safe_reply_text(update.message, "Not authorized.", parse_mode=None)
        return

    all_cases = await get_all_cases_for_agent(user.id)
    t_str = today_str()
    w_str = week_start_str()
    today = [c for c in all_cases if chicago_date_str(c.get("assigned_at")) == t_str]
    this_week = [c for c in all_cases if chicago_date_str(c.get("assigned_at")) >= w_str]
    done_all = [c for c in all_cases if c.get("status") == "done"]
    done_week = [c for c in this_week if c.get("status") == "done"]
    active_cnt = sum(1 for c in all_cases if c.get("status") in ("assigned", "reported"))

    text = (
        f"📊 <b>Your Stats</b>\n\n"
        f"Active now: {active_cnt}\n"
        f"Today:      {len(today)} assigned  |  {sum(1 for c in today if c.get('status') == 'done')} closed\n"
        f"This week:  {len(this_week)} assigned  |  {len(done_week)} closed\n"
        f"All time:   {len(all_cases)} total  |  {len(done_all)} closed"
    )
    await safe_reply_text(update.message, text)


# ── /casehistory ──────────────────────────────────────────────────────────────

async def cmd_casehistory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_admin(user.id):
        await safe_reply_text(update.message, "Not authorized.", parse_mode=None)
        return

    all_cases = await get_all_cases_for_agent(user.id)
    closed_only = [c for c in all_cases if c.get("status") == "done"]

    if not closed_only:
        await safe_reply_text(update.message, "No closed cases yet.", parse_mode=None)
        return

    ctx.user_data["history_msg_ids"] = []
    await _send_history_page(update.message, user.id, page=0, cases=closed_only, ctx=ctx)


async def _send_history_page(target, agent_id: int, page: int, cases=None, ctx=None):
    bot = target.get_bot()
    old_ids = list(ctx.user_data.pop("history_msg_ids", []) or []) if ctx is not None else []
    if ctx is not None:
        ctx.user_data["history_msg_ids"] = []
    del_task = asyncio.create_task(delete_messages_batch(bot, agent_id, old_ids)) if old_ids else None
    try:
        if cases is None:
            all_cases = await get_all_cases_for_agent(agent_id)
            cases = [c for c in all_cases if c.get("status") == "done"]

        total = len(cases)
        if total == 0:
            await safe_send_message(bot, agent_id, "No closed cases yet.", parse_mode=None)
            return

        total_pages = ((total - 1) // CASES_PER_PAGE) + 1
        page = max(0, min(page, total_pages - 1))
        start = page * CASES_PER_PAGE
        end = min(start + CASES_PER_PAGE, total)
        batch = cases[start:end]

        async def _send(text: str, reply_markup=None):
            sent = await safe_send_message(bot, agent_id, text, reply_markup=reply_markup)
            if sent and ctx is not None:
                ids = ctx.user_data.setdefault("history_msg_ids", [])
                ids.append(sent.message_id)
                if len(ids) > 30:
                    del ids[:-30]
            return sent

        for i, case in enumerate(batch):
            num = start + i + 1
            text = (
                f"<b>Case {num}</b>\n\n"
                f"<b>Group:</b> {esc_html(case.get('group_name'))}\n"
                f"<b>Reported by:</b> {esc_html(case.get('driver_name'))}\n"
                f"<b>Issue:</b> {esc_html((case.get('description') or '—')[:120])}\n"
                f"<b>Closed:</b> {esc_html(_fmt_dt(case.get('closed_at')))}"
                + (f"\n<b>Note:</b> {esc_html(case.get('notes'))}" if case.get("notes") else "")
                + (
                    f"\n<b>Replaced part #:</b> {esc_html(case.get('replaced_part_numbers'))}"
                    if case.get("replaced_part_numbers")
                    else ""
                )
            )
            nav = []
            if i == len(batch) - 1:
                if page > 0:
                    nav.append(InlineKeyboardButton("← Prev", callback_data=f"histpage|{page - 1}"))
                if end < total:
                    nav.append(InlineKeyboardButton("Next →", callback_data=f"histpage|{page + 1}"))
            kb = InlineKeyboardMarkup([nav]) if nav else None
            await _send(text, reply_markup=kb)

        footer = f"Page {page + 1} of {total_pages}  ({total} closed)"
        delete_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Clear from chat", callback_data="hist_delete_chat"),
        ]])
        await _send(footer, reply_markup=delete_kb)
    finally:
        if del_task is not None:
            await del_task


async def cb_histpage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        page = int(query.data.split("|")[1])
        agent_id = query.from_user.id
        all_cases = await get_all_cases_for_agent(agent_id)
        closed_only = [c for c in all_cases if c.get("status") == "done"]
        await _send_history_page(query, agent_id, page, cases=closed_only, ctx=ctx)


async def cb_hist_delete_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        chat_id = query.from_user.id
        msg_ids = list(ctx.user_data.pop("history_msg_ids", []) or [])
        if query.message and query.message.message_id not in msg_ids:
            msg_ids.append(query.message.message_id)
        await delete_messages_batch(ctx.bot, chat_id, msg_ids)


# ── Compatibility stubs ───────────────────────────────────────────────────────

async def cb_solve_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from bot_app.handlers.report_handler import cb_report_entry
    return await cb_report_entry(update, ctx)


async def cb_solve_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        await safe_edit_message_text(query, "Use /mycases to manage your cases.", parse_mode=None, reply_markup=None)


async def cb_solve_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        ctx.user_data.pop("solving_case_id", None)
        _clear_close_parts(ctx)
        case_id = query.data.split("|")[1]
        case = await get_case(case_id)
        if case and case.get("status") in ("assigned", "reported"):
            await safe_edit_message_text(
                query,
                _active_case_text(case),
                reply_markup=_active_case_keyboard(case["id"], case.get("status", "assigned")),
            )
        else:
            await safe_edit_message_text(query, "Case not found.", parse_mode=None, reply_markup=None)


async def cmd_solve_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _clear_close_parts(ctx)
    ctx.user_data.pop("solving_case_id", None)
    ctx.user_data.pop("report_case_id", None)
    ctx.user_data.pop("report_handler", None)
    await safe_reply_text(update.message, "Cancelled. Use /mycases to see your cases.", parse_mode=None)
    return ConversationHandler.END


# ── Close — safe confirmation + replaced part numbers + Option 1 summary ─────

async def cb_close_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tap ✅ Solve -> shows case details with Close / Cancel buttons."""
    query = update.callback_query
    async with concurrent_ack(query):
        case_id = query.data.split("|")[1]
        case = await get_case(case_id)

        if not case or case.get("status") not in ("assigned", "reported"):
            await safe_edit_message_text(query, "This case is no longer active.", parse_mode=None, reply_markup=None)
            return ConversationHandler.END

        _clear_close_parts(ctx)
        ctx.user_data.pop("report_awaiting", None)
        await safe_edit_message_text(
            query,
            f"📋 <b>Close Case</b>\n\n"
            f"📌 <b>Group:</b> {esc_html(case.get('group_name'))}\n"
            f"👤 <b>Driver:</b> {esc_html(case.get('driver_name'))}\n"
            f"📝 <b>Issue:</b> {esc_html((case.get('description') or '—')[:200])}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Close",  callback_data=f"close_confirm|{case_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"close_cancel|{case_id}"),
            ]]),
        )
        return ConversationHandler.END


async def cb_close_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ask about replaced parts BEFORE committing the close."""
    query = update.callback_query
    async with concurrent_ack(query):
        case_id = query.data.split("|", 1)[1]
        case = await get_case(case_id)
        if not case or case.get("status") not in ("assigned", "reported"):
            _clear_close_parts(ctx)
            await safe_edit_message_text(query, "This case is no longer active.", parse_mode=None, reply_markup=None)
            return
        ctx.user_data.pop("report_awaiting", None)
        ctx.user_data["close_parts_case_id"] = case_id
        await safe_edit_message_text(
            query,
            "<b>Before closing this case</b>\n\n"
            "Were any parts replaced?\n"
            "If yes, record the actual part numbers for Kurtex AI history.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Yes, parts replaced", callback_data=f"close_parts_yes|{case_id}"),
                    InlineKeyboardButton("No", callback_data=f"close_parts_no|{case_id}"),
                ],
                [InlineKeyboardButton("Cancel", callback_data=f"close_parts_cancel|{case_id}")],
            ]),
        )


def _clear_close_parts(ctx):
    ctx.user_data.pop("close_parts_case_id", None)


async def _show_remaining_after(agent_id: int, delay: int = 0):
    """Deprecated no-op kept for offline test monkeypatch compatibility (Option 1 replaces flood)."""
    return None


async def _finish_close(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    case_id: str,
    *,
    parts_replaced: bool,
    replaced_parts: list[str],
):
    """Persist close, then show a clean summary + next-case action (Option 1, zero DM flood)."""
    case = await get_case(case_id)
    if not case or case.get("status") not in ("assigned", "reported"):
        _clear_close_parts(ctx)
        message = "This case is no longer active."
        if getattr(update, "callback_query", None):
            await safe_edit_message_text(update.callback_query, message, parse_mode=None, reply_markup=None)
        else:
            await safe_reply_text(update.message, message, parse_mode=None)
        return
    try:
        saved = await close_case(
            case_id,
            notes=None,
            parts_replaced=parts_replaced,
            replaced_parts=replaced_parts,
        )
        if not saved or saved.get("status") != "done":
            raise RuntimeError("Case close was not saved")
    except Exception:
        logger.exception("Failed to close case %s with replacement details", case_id)
        message = "Could not save this case. Please try again from /mycases."
        if getattr(update, "callback_query", None):
            await safe_edit_message_text(update.callback_query, message, parse_mode=None, reply_markup=None)
        else:
            await safe_reply_text(update.message, message, parse_mode=None)
        _clear_close_parts(ctx)
        return

    _clear_close_parts(ctx)
    user_id = update.effective_user.id
    counts = await async_count_active_cases_for_agent(user_id)

    lines = [
        "✅ <b>Case closed!</b>",
        "",
        f"📌 <b>Group:</b> {esc_html(case.get('group_name'))}",
        f"👤 <b>Driver:</b> {esc_html(case.get('driver_name'))}",
    ]
    if parts_replaced:
        part_text = esc_html(", ".join(replaced_parts)) if replaced_parts else "not provided"
        lines.append(f"🔧 <b>Replaced part #:</b> {part_text}")
    else:
        lines.append("🔧 <b>Parts replaced:</b> No")
    lines.append("💾 Saved to case history for Kurtex AI.")
    lines.append("")
    if counts["total"] > 0:
        lines.append(
            f"📊 <b>Remaining active:</b> {counts['total']} "
            f"({counts['assigned']} non-reported · {counts['reported']} reported)"
        )
        next_label = "➡️ Next Non-Reported Case" if counts["assigned"] > 0 else "➡️ Next Active Case"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(next_label, callback_data="mycases_next_case"),
            InlineKeyboardButton("📋 View /mycases", callback_data="mycases_page|all|0"),
        ]])
    else:
        lines.append("🎉 All caught up — no active cases remaining!")
        kb = None

    text = "\n".join(lines)
    if getattr(update, "callback_query", None):
        await safe_edit_message_text(update.callback_query, text, reply_markup=kb)
    else:
        await safe_reply_text(update.message, text, reply_markup=kb)
    await _show_remaining_after(user_id, 0)


async def cb_close_parts_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        case_id = query.data.split("|", 1)[1]
        if ctx.user_data.get("close_parts_case_id") != case_id:
            await safe_edit_message_text(query, "This close step expired. Open the case again from /mycases.", parse_mode=None)
            return
        await safe_edit_message_text(
            query,
            "<b>Part numbers replaced</b>\n\n"
            "Send the actual part number(s) in your next message.\n"
            "Use one per line or separate multiple numbers with commas.\n\n"
            "Example: <code>AB-12345, 456789</code>\n\n"
            "Do not guess. If you do not have the number, use the button below.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Part # unavailable", callback_data=f"close_parts_skip|{case_id}")],
                [InlineKeyboardButton("Cancel", callback_data=f"close_parts_cancel|{case_id}")],
            ]),
        )


async def cb_close_parts_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        case_id = query.data.split("|", 1)[1]
        if ctx.user_data.get("close_parts_case_id") != case_id:
            await safe_edit_message_text(query, "This close step expired. Open the case again from /mycases.", parse_mode=None)
            return
        await _finish_close(update, ctx, case_id, parts_replaced=False, replaced_parts=[])


async def cb_close_parts_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        case_id = query.data.split("|", 1)[1]
        if ctx.user_data.get("close_parts_case_id") != case_id:
            await safe_edit_message_text(query, "This close step expired. Open the case again from /mycases.", parse_mode=None)
            return
        await _finish_close(update, ctx, case_id, parts_replaced=True, replaced_parts=[])


async def cb_close_parts_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        _clear_close_parts(ctx)
        case_id = query.data.split("|", 1)[1]
        case = await get_case(case_id)
        if case and case.get("status") in ("assigned", "reported"):
            await safe_edit_message_text(
                query,
                _active_case_text(case),
                reply_markup=_active_case_keyboard(case_id, case.get("status", "assigned")),
            )
        else:
            await safe_edit_message_text(query, "This case is no longer active.", parse_mode=None, reply_markup=None)


async def recv_close_parts_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Plain Telegram chat input for the optional replacement part numbers."""
    case_id = ctx.user_data.get("close_parts_case_id")
    if not case_id:
        return
    raw = (update.message.text or "").strip()
    numbers = [part.strip() for part in re.split(r"[,;\n]+", raw) if part.strip()]
    if (
        not numbers
        or len(numbers) > 20
        or any(len(part) > 80 for part in numbers)
        or len(raw) > 1600
    ):
        await safe_reply_text(
            update.message,
            "Please send 1-20 actual part numbers (max 80 characters each), "
            "separated by commas or new lines. Or select 'Part # unavailable'.",
            parse_mode=None,
        )
        return
    if any(part.casefold() in {"none", "no", "n/a", "unknown", "nu", "nimic", "-"} for part in numbers):
        await safe_reply_text(
            update.message,
            "Please enter actual part numbers, or select 'Part # unavailable' on the previous message.",
            parse_mode=None,
        )
        return
    await _finish_close(update, ctx, case_id, parts_replaced=True, replaced_parts=numbers)


async def cb_close_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        ctx.user_data.pop("solving_case_id", None)
        _clear_close_parts(ctx)
        case_id = query.data.split("|")[1]
        case = await get_case(case_id)
        if case and case.get("status") in ("assigned", "reported"):
            await safe_edit_message_text(
                query,
                _active_case_text(case),
                reply_markup=_active_case_keyboard(case["id"], case.get("status", "assigned")),
            )
        else:
            await safe_edit_message_text(query, "Case not found.", parse_mode=None, reply_markup=None)


# ── /done ─────────────────────────────────────────────────────────────────────

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_admin(user.id):
        await safe_reply_text(update.message, "Not authorized.", parse_mode=None)
        return

    today_cases = await get_cases_for_agent_today(user.id)
    closed_today = [c for c in today_cases if c.get("status") == "done"]

    if not closed_today:
        await safe_reply_text(update.message, "No cases closed today yet.", parse_mode=None)
        return

    lines = [f"<b>Today's closed cases: {len(closed_today)}</b>\n"]
    for i, c in enumerate(closed_today, 1):
        note = f"\n   Note: {esc_html(c.get('notes'))}" if c.get("notes") else ""
        lines.append(
            f"{i}. {esc_html(c.get('driver_name'))} — {esc_html(c.get('group_name'))}\n"
            f"   Closed: {esc_html(_fmt_dt(c.get('closed_at')))}"
            f"{note}"
        )
    await safe_reply_text(update.message, "\n".join(lines))


async def cb_done_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        case_id = query.data.split("|")[1]
        case = await get_case(case_id)

        if not case:
            await safe_edit_message_text(query, "Case not found.", parse_mode=None, reply_markup=None)
            return ConversationHandler.END

        await safe_edit_message_text(
            query,
            f"📋 <b>Close Case</b>\n\n"
            f"📌 <b>Group:</b> {esc_html(case.get('group_name'))}\n"
            f"👤 <b>Driver:</b> {esc_html(case.get('driver_name'))}\n"
            f"📝 <b>Issue:</b> {esc_html((case.get('description') or '—')[:200])}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Close",  callback_data=f"close_confirm|{case_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"close_cancel|{case_id}"),
            ]]),
        )
        return ConversationHandler.END


# ── Backward-compat stubs ─────────────────────────────────────────────────────

async def cb_delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    case_id = query.data.split("|")[1]
    query.data = f"close_ask|{case_id}"
    return await cb_close_ask(update, ctx)


async def cb_delete_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        await safe_edit_message_text(query, "Cancelled.", parse_mode=None, reply_markup=None)


async def cb_delete_keep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with concurrent_ack(query):
        case_id = query.data.split("|")[1]
        case = await get_case(case_id)
        if case and case.get("status") in ("assigned", "reported"):
            await safe_edit_message_text(
                query,
                _active_case_text(case),
                reply_markup=_active_case_keyboard(case["id"], case.get("status", "assigned")),
            )
        else:
            await safe_edit_message_text(query, "Case not found.", parse_mode=None, reply_markup=None)


def get_solve_conversation():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_done_pick, pattern=r"^done_pick\|"),
            CallbackQueryHandler(cb_close_ask, pattern=r"^close_ask\|"),
        ],
        states={},
        fallbacks=[CommandHandler("cancel", cmd_solve_cancel)],
        per_message=False,
        allow_reentry=True,
    )
