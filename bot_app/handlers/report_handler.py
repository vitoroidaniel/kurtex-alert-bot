"""handlers/report_handler.py -- Streamlined chat report flow (no media step)."""

from __future__ import annotations

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from backend.core.config import config
from backend.storage.case_store import (
    async_get_case,
    async_save_maintenance_report,
)
from bot_app.telegram_utils import (
    concurrent_ack,
    esc_html,
    safe_answer,
    safe_edit_message_text,
    safe_reply_text,
    safe_send_message,
)

logger = logging.getLogger(__name__)


def _ack_report_ctx(query, step: str):
    """Log step and run Telegram button acknowledgment concurrently with handler work."""
    user_id = getattr(getattr(query, "from_user", None), "id", None)
    logger.info("[REPORT] %s ENTER data=%r user=%s", step, getattr(query, "data", None), user_id)
    return concurrent_ack(query)


async def _ack_report_callback(query, step: str):
    """Backward-compatible helper to log step and acknowledge inline button."""
    user_id = getattr(getattr(query, "from_user", None), "id", None)
    logger.info("[REPORT] %s ENTER data=%r user=%s", step, getattr(query, "data", None), user_id)
    await safe_answer(query)


def _report_ready(ctx) -> bool:
    return isinstance(ctx.user_data.get("report"), dict)


async def _missing_report_session(query):
    await safe_edit_message_text(
        query,
        "This report session has expired. Open the active case and press Report again.",
        parse_mode=None,
        reply_markup=None,
    )


(
    ASK_TYPE,
    ASK_UNIT,
    ASK_DRIVER,
    ASK_ISSUE,
    ASK_LOAD,
    ASK_PICKUP,
    ASK_DELIVERY,
    ASK_LOCATION,
    ASK_SETPOINT,
    ASK_CURRENT_TEMP,
    ASK_TEMP_RECORDER,
    ASK_COMMENTS,
    ASK_PRIORITY,
    CONFIRM,
    ASK_EDIT_FIELD,
    ASK_EDIT_VALUE,
) = range(16)

SKIP_KB = InlineKeyboardMarkup([[InlineKeyboardButton("Skip", callback_data="rpt_skip")]])

LOAD_TYPE_KB = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("JBS Load", callback_data="rpt_loadtype|jbs"),
        InlineKeyboardButton("Broker Load", callback_data="rpt_loadtype|broker"),
    ],
    [
        InlineKeyboardButton("Meijer Load", callback_data="rpt_loadtype|meijer"),
        InlineKeyboardButton("OpenDeck", callback_data="rpt_loadtype|opendeck"),
    ],
    [InlineKeyboardButton("Empty", callback_data="rpt_loadtype|empty")],
])

VTYPE_LABELS = {
    "truck":   "Truck",
    "trailer": "Trailer",
    "reefer":  "Reefer",
}

PRIORITY_META = {
    "low":    {"icon": "🟢", "level": "Low"},
    "medium": {"icon": "🟡", "level": "Medium"},
    "high":   {"icon": "🔴", "level": "High"},
}


def _needs_reefer_temps(report: dict) -> bool:
    """Empty reefer loads do not require temperature/recorder questions."""
    return (
        report.get("vehicle_type") == "reefer"
        and str(report.get("load", "")).strip().casefold() != "empty"
    )


def _save_load_choice(report: dict, label: str) -> None:
    """Keep the selected load and its dependent report fields consistent."""
    report["load"] = label
    if label.strip().casefold() == "empty":
        report["pickup"] = "—"
        report["delivery"] = "—"
        for key in ("setpoint", "current_temp", "temp_recorder"):
            report.pop(key, None)


def _priority_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Low",    callback_data="rpt_priority|low"),
        InlineKeyboardButton("🟡 Medium", callback_data="rpt_priority|medium"),
        InlineKeyboardButton("🔴 High",   callback_data="rpt_priority|high"),
    ]])


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Send Report", callback_data="rpt_confirm|yes"),
            InlineKeyboardButton("❌ Cancel",      callback_data="rpt_confirm|no"),
        ],
        [InlineKeyboardButton("✏️ Edit", callback_data="rpt_edit")],
    ])


def _esc(text) -> str:
    return esc_html(text, fallback="—")


def _build_report(d: dict) -> str:
    vtype = d.get("vehicle_type", "truck")
    priority_key = d.get("priority", "low")
    p = PRIORITY_META.get(priority_key, PRIORITY_META["low"])
    unit = d.get("unit_number", "")
    load = d.get("load", "")

    if vtype == "truck":
        unit_line = f"<b>Truck:</b> {_esc(unit)}" if unit else "<b>Truck:</b> —"
    else:
        unit_line = f"<b>Trailer:</b> {_esc(unit)}" if unit else "<b>Trailer:</b> —"

    lines = [
        f"{p['icon']} <b>Case Report — {esc_html(VTYPE_LABELS.get(vtype, vtype.title()))}</b>",
        f"Priority: <b>{esc_html(p['level'])}</b>",
        "",
        unit_line,
        f"<b>Driver:</b> {_esc(d.get('driver', '—'))}",
        "",
        f"<b>Issue:</b> {_esc(d.get('issue', '—'))}",
        "",
    ]

    if load and load != "—":
        lines.append(f"<b>{_esc(load)}</b>")
        if load.lower() != "empty":
            lines.append(f"Pick up Location/Time: {_esc(d.get('pickup', '—'))}")
            lines.append(f"Delivery Location/Time: {_esc(d.get('delivery', '—'))}")
        lines.append(f"Current Location: {_esc(d.get('location', '—'))}")
    else:
        lines.append(f"Current Location: {_esc(d.get('location', '—'))}")

    if _needs_reefer_temps(d):
        lines += [
            "",
            f"<b>Setpoint:</b> {_esc(d.get('setpoint', '—'))}",
            f"<b>Current temp:</b> {_esc(d.get('current_temp', '—'))}",
            f"<b>Temp recorder:</b> {_esc(d.get('temp_recorder', '—'))}",
        ]

    if d.get("comments"):
        lines += ["", f"<b>Comments:</b> {_esc(d.get('comments'))}"]

    lines += ["", f"<b>Reported by:</b> {_esc(d.get('handler', '—'))}"]
    return "\n".join(lines)


def _driver_prompt(ctx) -> tuple[str, InlineKeyboardMarkup | None]:
    prefill = (ctx.user_data.get("report_prefill") or {}).get("driver", "").strip()
    if prefill:
        label = prefill[:36] + ("..." if len(prefill) > 36 else "")
        return (
            f"Driver (from alert: <b>{esc_html(prefill)}</b>)\nType a name or tap below:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(f"👤 Use: {label}", callback_data="rpt_prefill|driver"),
            ]]),
        )
    return "Driver:", None


def _issue_prompt(ctx) -> tuple[str, InlineKeyboardMarkup | None]:
    prefill = (ctx.user_data.get("report_prefill") or {}).get("issue", "").strip()
    if prefill:
        return (
            f"Issue description:\n<i>Alert text: {esc_html(prefill[:160])}</i>\n\nType details or tap below:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("📝 Use alert issue", callback_data="rpt_prefill|issue"),
            ]]),
        )
    return "Issue description:", None


async def cb_report_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Entry point from Report button (solve|case_id)."""
    query = update.callback_query
    async with _ack_report_ctx(query, "ENTRY"):
        case_id = query.data.split("|")[1]

        case = await async_get_case(case_id)
        if not case or case.get("status") not in ("assigned", "reported"):
            await safe_edit_message_text(query, "This case is no longer active.", parse_mode=None, reply_markup=None)
            return ConversationHandler.END

        existing_case_id = ctx.user_data.get("report_case_id")
        if existing_case_id and existing_case_id != case_id:
            logger.info(
                "[REPORT] Switching draft user=%s from_case=%s to_case=%s",
                update.effective_user.id,
                existing_case_id,
                case_id,
            )
            for key in ("report", "report_awaiting", "editing_field", "report_prefill"):
                ctx.user_data.pop(key, None)

        ctx.user_data.pop("close_parts_case_id", None)
        user = update.effective_user
        handler_name = f"{user.first_name} {user.last_name or ''}".strip()
        ctx.user_data["report_case_id"] = case_id
        ctx.user_data["report_handler"] = handler_name
        ctx.user_data["report_prefill"] = {
            "driver": (case.get("driver_name") or "").strip(),
            "issue": (case.get("description") or "").strip(),
        }
        ctx.bot_data.setdefault("busy_agents", set()).add(user.id)

        await safe_edit_message_text(
            query,
            f"📋 <b>Report</b>\n\n"
            f"Reported by: {esc_html(case.get('driver_name'))} — {esc_html(case.get('group_name'))}\n"
            f"Issue: {esc_html((case.get('description') or '')[:120])}\n\n"
            "Select vehicle type:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🚛 Truck",   callback_data="rpt_type|truck"),
                InlineKeyboardButton("🚚 Trailer", callback_data="rpt_type|trailer"),
                InlineKeyboardButton("❄️ Reefer",  callback_data="rpt_type|reefer"),
            ]]),
        )
        return ASK_TYPE


async def cb_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "TYPE"):
        vtype = query.data.split("|")[1]
        report_case_id = ctx.user_data.get("report_case_id")
        report_handler = ctx.user_data.get("report_handler")
        if not report_case_id:
            logger.warning("[REPORT] TYPE without active case user=%s", update.effective_user.id)
            await _missing_report_session(query)
            return ConversationHandler.END

        case = await async_get_case(report_case_id)
        if not case or case.get("status") not in ("assigned", "reported"):
            await _missing_report_session(query)
            return ConversationHandler.END

        ctx.user_data["report"] = {"media": [], "vehicle_type": vtype}
        ctx.user_data["report_case_id"] = report_case_id
        if report_handler:
            ctx.user_data["report"]["handler"] = report_handler
        if not ctx.user_data.get("report_prefill"):
            ctx.user_data["report_prefill"] = {
                "driver": (case.get("driver_name") or "").strip(),
                "issue": (case.get("description") or "").strip(),
            }

        unit_prompt = "Truck number:" if vtype == "truck" else "Trailer number:"
        label = {"truck": "🚛 Truck", "trailer": "🚚 Trailer", "reefer": "❄️ Reefer"}[vtype]
        await safe_edit_message_text(
            query,
            f"Type: <b>{esc_html(label)}</b>\n\n{unit_prompt}",
            reply_markup=None,
        )
        return ASK_UNIT


async def recv_unit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["unit_number"] = update.message.text.strip()
    prompt, kb = _driver_prompt(ctx)
    await safe_reply_text(update.message, prompt, reply_markup=kb)
    return ASK_DRIVER


async def recv_driver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["driver"] = update.message.text.strip()
    prompt, kb = _issue_prompt(ctx)
    await safe_reply_text(update.message, prompt, reply_markup=kb)
    return ASK_ISSUE


async def recv_issue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["issue"] = update.message.text.strip()
    await safe_reply_text(update.message, "Load type:", parse_mode=None, reply_markup=LOAD_TYPE_KB)
    return ASK_LOAD


async def cb_prefill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "PREFILL"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        field = query.data.split("|", 1)[1]
        prefill = ctx.user_data.get("report_prefill") or {}
        if field == "driver":
            val = prefill.get("driver", "").strip() or "—"
            ctx.user_data["report"]["driver"] = val
            prompt, kb = _issue_prompt(ctx)
            await safe_edit_message_text(query, f"Driver: <b>{esc_html(val)}</b>\n\n{prompt}", reply_markup=kb)
            return ASK_ISSUE
        if field == "issue":
            val = prefill.get("issue", "").strip() or "—"
            ctx.user_data["report"]["issue"] = val
            await safe_edit_message_text(query, "Load type:", parse_mode=None, reply_markup=LOAD_TYPE_KB)
            return ASK_LOAD
        return ASK_ISSUE


async def cb_loadtype(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "LOAD_TYPE"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        ltype = query.data.split("|", 1)[1]
        label_map = {
            "jbs": "JBS Load",
            "broker": "Broker Load",
            "meijer": "Meijer Load",
            "opendeck": "OpenDeck",
            "empty": "Empty",
        }
        label = label_map.get(ltype, ltype.title())
        _save_load_choice(ctx.user_data["report"], label)

        if ltype == "empty":
            ctx.user_data["report"]["pickup"] = "—"
            ctx.user_data["report"]["delivery"] = "—"
            await safe_edit_message_text(
                query,
                f"<b>{esc_html(label)}</b>\n\nCurrent Location:",
                reply_markup=SKIP_KB,
            )
            return ASK_LOCATION
        await safe_edit_message_text(
            query,
            f"<b>{esc_html(label)}</b>\n\nPick up Location / Time:",
            reply_markup=SKIP_KB,
        )
        return ASK_PICKUP


async def recv_load(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    choices = {
        "jbs load": "JBS Load",
        "broker load": "Broker Load",
        "meijer load": "Meijer Load",
        "opendeck": "OpenDeck",
        "empty": "Empty",
    }
    label = choices.get(raw.casefold(), raw)
    _save_load_choice(ctx.user_data["report"], label)
    if label.casefold() == "empty":
        await safe_reply_text(update.message, "Current Location:", parse_mode=None, reply_markup=SKIP_KB)
        return ASK_LOCATION
    await safe_reply_text(update.message, "Pick up Location / Time:", parse_mode=None, reply_markup=SKIP_KB)
    return ASK_PICKUP


async def recv_pickup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["pickup"] = update.message.text.strip()
    await safe_reply_text(update.message, "Delivery Location / Time:", parse_mode=None, reply_markup=SKIP_KB)
    return ASK_DELIVERY


async def recv_delivery(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["delivery"] = update.message.text.strip()
    await safe_reply_text(update.message, "Current Location:", parse_mode=None, reply_markup=SKIP_KB)
    return ASK_LOCATION


async def recv_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["location"] = update.message.text.strip()
    if _needs_reefer_temps(ctx.user_data["report"]):
        await safe_reply_text(update.message, "Setpoint temperature (e.g. -10°C):", parse_mode=None, reply_markup=SKIP_KB)
        return ASK_SETPOINT
    ctx.user_data["report_awaiting"] = "comments"
    await safe_reply_text(update.message, "Comments:", parse_mode=None, reply_markup=SKIP_KB)
    return ASK_COMMENTS


async def recv_setpoint(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["setpoint"] = update.message.text.strip()
    await safe_reply_text(update.message, "Current temperature:", parse_mode=None, reply_markup=SKIP_KB)
    return ASK_CURRENT_TEMP


async def recv_current_temp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["current_temp"] = update.message.text.strip()
    await safe_reply_text(
        update.message,
        "Temp recorder: Y or N?",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Y", callback_data="rpt_temprec|Y"),
            InlineKeyboardButton("N", callback_data="rpt_temprec|N"),
        ]]),
    )
    return ASK_TEMP_RECORDER


async def cb_temp_recorder(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "TEMP_RECORDER"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        ctx.user_data["report"]["temp_recorder"] = query.data.split("|")[1]
        ctx.user_data["report_awaiting"] = "comments"
        await safe_edit_message_text(query, "Comments:", parse_mode=None, reply_markup=SKIP_KB)
        return ASK_COMMENTS


async def _ask_priority(target, ctx, *, edit: bool = False):
    ctx.user_data.pop("report_awaiting", None)
    vtype = ctx.user_data.get("report", {}).get("vehicle_type", "truck")
    vtype_label = VTYPE_LABELS.get(vtype, vtype.title())
    text = f"Vehicle: <b>{esc_html(vtype_label)}</b>\n\nSelect priority:"
    if edit:
        await safe_edit_message_text(target, text, reply_markup=_priority_kb())
    else:
        await safe_reply_text(target, text, reply_markup=_priority_kb())
    return ASK_PRIORITY


async def recv_comments(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _report_ready(ctx):
        await safe_reply_text(
            update.message,
            "This report session has expired. Open the active case and press Report again.",
            parse_mode=None,
        )
        return ConversationHandler.END
    ctx.user_data["report"]["comments"] = update.message.text.strip()
    return await _ask_priority(update.message, ctx, edit=False)


async def cb_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "SKIP"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        report = ctx.user_data.get("report", {})
        needs_temps = _needs_reefer_temps(report)

        if "pickup" not in report:
            report["pickup"] = "—"
            await safe_edit_message_text(query, "Delivery Location / Time:", parse_mode=None, reply_markup=SKIP_KB)
            return ASK_DELIVERY
        if "delivery" not in report:
            report["delivery"] = "—"
            await safe_edit_message_text(query, "Current Location:", parse_mode=None, reply_markup=SKIP_KB)
            return ASK_LOCATION
        if "location" not in report:
            report["location"] = "—"
            if needs_temps:
                await safe_edit_message_text(query, "Setpoint temperature:", parse_mode=None, reply_markup=SKIP_KB)
                return ASK_SETPOINT
            ctx.user_data["report_awaiting"] = "comments"
            await safe_edit_message_text(query, "Comments:", parse_mode=None, reply_markup=SKIP_KB)
            return ASK_COMMENTS
        if needs_temps and "setpoint" not in report:
            report["setpoint"] = "—"
            await safe_edit_message_text(query, "Current temperature:", parse_mode=None, reply_markup=SKIP_KB)
            return ASK_CURRENT_TEMP
        if needs_temps and "current_temp" not in report:
            report["current_temp"] = "—"
            await safe_edit_message_text(
                query,
                "Temp recorder: Y or N?",
                parse_mode=None,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Y", callback_data="rpt_temprec|Y"),
                    InlineKeyboardButton("N", callback_data="rpt_temprec|N"),
                ]]),
            )
            return ASK_TEMP_RECORDER
        if "comments" not in report:
            report["comments"] = None
            return await _ask_priority(query, ctx, edit=True)
        return await _ask_priority(query, ctx, edit=True)


async def recover_report_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Recover report comments if PTB conversation state went stale."""
    if ctx.user_data.get("report_awaiting") != "comments" or not _report_ready(ctx):
        return
    logger.warning(
        "[REPORT] recovering COMMENTS from stale conversation state user=%s",
        update.effective_user.id if update.effective_user else None,
    )
    await recv_comments(update, ctx)


async def cb_priority(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "PRIORITY"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        ctx.user_data["report"]["priority"] = query.data.split("|")[1]
        await _show_preview(query, ctx, edit=True)
        return CONFIRM


async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "CONFIRM"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        action = query.data.split("|")[1]

        if action == "no":
            for key in ("report_awaiting", "report", "report_case_id", "report_handler", "report_prefill"):
                ctx.user_data.pop(key, None)
            if "busy_agents" in ctx.bot_data and update.effective_user:
                ctx.bot_data["busy_agents"].discard(update.effective_user.id)
            await safe_edit_message_text(query, "Report cancelled.", parse_mode=None, reply_markup=None)
            return ConversationHandler.END

        data = ctx.user_data.get("report") or {}
        dest_id = config.REPORTS_GROUP_ID
        if not dest_id:
            await safe_edit_message_text(query, "No reports group configured.", parse_mode=None, reply_markup=None)
            return ConversationHandler.END

        report_text = _build_report(data)
        case_id = ctx.user_data.get("report_case_id")

        try:
            await safe_send_message(ctx.bot, dest_id, report_text)
            logger.info("Report sent to %s", dest_id)

            if case_id:
                saved = await async_save_maintenance_report(case_id, dict(data), report_text, [])
                if saved:
                    logger.info("Case %s fleet data saved: vtype=%s", case_id, data.get("vehicle_type"))
                else:
                    logger.warning("Case %s not found in DB for fleet data save", case_id)

            for key in ("report_awaiting", "report", "report_case_id", "report_handler", "report_prefill"):
                ctx.user_data.pop(key, None)
            if "busy_agents" in ctx.bot_data and update.effective_user:
                ctx.bot_data["busy_agents"].discard(update.effective_user.id)

            await safe_edit_message_text(query, "✅ <b>Report sent!</b>", reply_markup=None)
            return ConversationHandler.END

        except TelegramError as exc:
            logger.error("Failed to send report: %s", exc)
            # Keep draft intact so user can retry without losing their work.
            await safe_edit_message_text(
                query,
                "⚠️ Failed to send report due to a Telegram error. Your draft was preserved.",
                parse_mode=None,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Retry Send", callback_data="rpt_confirm|yes"),
                    InlineKeyboardButton("❌ Cancel",     callback_data="rpt_confirm|no"),
                ]]),
            )
            return CONFIRM


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for key in ("report_awaiting", "report", "report_case_id", "report_handler", "report_prefill", "editing_field"):
        ctx.user_data.pop(key, None)
    if "busy_agents" in ctx.bot_data and update.effective_user:
        ctx.bot_data["busy_agents"].discard(update.effective_user.id)
    await safe_reply_text(update.message, "Report cancelled. Use /mycases to continue.", parse_mode=None)
    return ConversationHandler.END


async def _show_preview(target, ctx, edit: bool = True):
    report = ctx.user_data.get("report", {})
    preview = _build_report(report)
    text = f"<b>Preview — confirm and send?</b>\n\n{preview}"
    if edit and hasattr(target, "edit_message_text"):
        await safe_edit_message_text(target, text, reply_markup=_confirm_kb())
    else:
        msg = target.message if hasattr(target, "message") else target
        await safe_reply_text(msg, text, reply_markup=_confirm_kb())


def _edit_fields_kb(vtype: str, report: dict | None = None) -> InlineKeyboardMarkup:
    fields = [
        ("Unit number",   "rpt_editfield|unit"),
        ("Driver name",   "rpt_editfield|driver"),
        ("Issue",         "rpt_editfield|issue"),
        ("Load type",     "rpt_editfield|load"),
        ("Pick up",       "rpt_editfield|pickup"),
        ("Delivery",      "rpt_editfield|delivery"),
        ("Location",      "rpt_editfield|location"),
        ("Comments",      "rpt_editfield|comments"),
        ("Priority",      "rpt_editfield|priority"),
    ]
    if vtype == "reefer" and _needs_reefer_temps(report or {"vehicle_type": vtype}):
        fields[7:7] = [
            ("Setpoint",      "rpt_editfield|setpoint"),
            ("Current temp",  "rpt_editfield|current_temp"),
            ("Temp recorder", "rpt_editfield|temp_recorder"),
        ]
    rows = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in fields]
    rows.append([InlineKeyboardButton("↩️ Back to preview", callback_data="rpt_edit_back")])
    return InlineKeyboardMarkup(rows)


async def cb_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "EDIT"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        vtype = ctx.user_data.get("report", {}).get("vehicle_type", "truck")
        await safe_edit_message_text(
            query,
            "Which field would you like to edit?",
            parse_mode=None,
            reply_markup=_edit_fields_kb(vtype, ctx.user_data["report"]),
        )
        return ASK_EDIT_FIELD


async def cb_edit_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "EDIT_BACK"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        await _show_preview(query, ctx, edit=True)
        return CONFIRM


async def cb_edit_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "EDIT_FIELD"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        field = query.data.split("|")[1]
        ctx.user_data["editing_field"] = field
        prompts = {
            "unit": "Enter new unit number:",
            "driver": "Enter new driver name:",
            "issue": "Enter new issue description:",
            "load": "Enter load type:",
            "pickup": "Enter new pick up location/time:",
            "delivery": "Enter new delivery location/time:",
            "location": "Enter new current location:",
            "setpoint": "Enter new setpoint temperature:",
            "current_temp": "Enter new current temperature:",
            "temp_recorder": "Temp recorder: Y or N?",
            "comments": "Enter new comments:",
            "priority": "Select new priority:",
        }
        if field == "load":
            await safe_edit_message_text(
                query,
                "Select load type:",
                parse_mode=None,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("JBS Load", callback_data="rpt_editval|JBS Load"),
                        InlineKeyboardButton("Broker Load", callback_data="rpt_editval|Broker Load"),
                    ],
                    [
                        InlineKeyboardButton("Meijer Load", callback_data="rpt_editval|Meijer Load"),
                        InlineKeyboardButton("OpenDeck", callback_data="rpt_editval|OpenDeck"),
                    ],
                    [InlineKeyboardButton("Empty", callback_data="rpt_editval|Empty")],
                ]),
            )
            return ASK_EDIT_VALUE
        if field == "temp_recorder":
            await safe_edit_message_text(
                query,
                prompts[field],
                parse_mode=None,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Y", callback_data="rpt_editval|Y"),
                    InlineKeyboardButton("N", callback_data="rpt_editval|N"),
                ]]),
            )
            return ASK_EDIT_VALUE
        if field == "priority":
            await safe_edit_message_text(
                query,
                prompts[field],
                parse_mode=None,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🟢 Low",    callback_data="rpt_editval|low"),
                    InlineKeyboardButton("🟡 Medium", callback_data="rpt_editval|medium"),
                    InlineKeyboardButton("🔴 High",   callback_data="rpt_editval|high"),
                ]]),
            )
            return ASK_EDIT_VALUE
        await safe_edit_message_text(query, prompts.get(field, "Enter new value:"), parse_mode=None)
        return ASK_EDIT_VALUE


async def cb_edit_val_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    async with _ack_report_ctx(query, "EDIT_VALUE"):
        if not _report_ready(ctx):
            await _missing_report_session(query)
            return ConversationHandler.END
        field = ctx.user_data.pop("editing_field", None)
        value = query.data.split("|")[1]
        if field:
            if field == "load":
                _save_load_choice(ctx.user_data["report"], value)
            else:
                ctx.user_data["report"]["unit_number" if field == "unit" else field] = value
        await _show_preview(query, ctx, edit=True)
        return CONFIRM


async def recv_edit_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    field = ctx.user_data.pop("editing_field", None)
    if field:
        if field == "load":
            _save_load_choice(ctx.user_data["report"], value)
        else:
            ctx.user_data["report"]["unit_number" if field == "unit" else field] = value
    await _show_preview(update.message, ctx, edit=False)
    return CONFIRM


async def cb_orphan_report_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Stop Telegram's spinner for a stale report button outside a live conversation."""
    query = update.callback_query
    logger.warning(
        "[REPORT] ORPHAN callback=%r user=%s; no active report conversation",
        getattr(query, "data", None),
        update.effective_user.id if update.effective_user else None,
    )
    await safe_answer(
        query,
        "This report session expired. Open the active case and press Report again.",
        show_alert=True,
    )


def get_report_conversation():
    private_text = filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND

    return ConversationHandler(
        name="kurtex_telegram_report_v1",
        persistent=True,
        entry_points=[
            CallbackQueryHandler(cb_report_entry, pattern=r"^solve\|"),
            CallbackQueryHandler(cb_type,         pattern=r"^rpt_type\|"),
        ],
        states={
            ASK_TYPE:          [CallbackQueryHandler(cb_type,          pattern=r"^rpt_type\|")],
            ASK_UNIT:          [MessageHandler(private_text,           recv_unit)],
            ASK_DRIVER:        [
                MessageHandler(private_text, recv_driver),
                CallbackQueryHandler(cb_prefill, pattern=r"^rpt_prefill\|driver$"),
            ],
            ASK_ISSUE:         [
                MessageHandler(private_text, recv_issue),
                CallbackQueryHandler(cb_prefill, pattern=r"^rpt_prefill\|issue$"),
            ],
            ASK_LOAD:          [
                CallbackQueryHandler(cb_loadtype,      pattern=r"^rpt_loadtype\|"),
                CallbackQueryHandler(cb_skip,          pattern=r"^rpt_skip$"),
                MessageHandler(private_text,           recv_load),
            ],
            ASK_PICKUP:        [
                MessageHandler(private_text,           recv_pickup),
                CallbackQueryHandler(cb_skip,          pattern=r"^rpt_skip$"),
            ],
            ASK_DELIVERY:      [
                MessageHandler(private_text,           recv_delivery),
                CallbackQueryHandler(cb_skip,          pattern=r"^rpt_skip$"),
            ],
            ASK_LOCATION:      [
                MessageHandler(private_text,           recv_location),
                CallbackQueryHandler(cb_skip,          pattern=r"^rpt_skip$"),
            ],
            ASK_SETPOINT:      [
                MessageHandler(private_text,           recv_setpoint),
                CallbackQueryHandler(cb_skip,          pattern=r"^rpt_skip$"),
            ],
            ASK_CURRENT_TEMP:  [
                MessageHandler(private_text,           recv_current_temp),
                CallbackQueryHandler(cb_skip,          pattern=r"^rpt_skip$"),
            ],
            ASK_TEMP_RECORDER: [CallbackQueryHandler(cb_temp_recorder, pattern=r"^rpt_temprec\|")],
            ASK_COMMENTS:      [
                MessageHandler(private_text,           recv_comments),
                CallbackQueryHandler(cb_skip,          pattern=r"^rpt_skip$"),
            ],
            ASK_PRIORITY:      [CallbackQueryHandler(cb_priority,      pattern=r"^rpt_priority\|")],
            CONFIRM:           [
                CallbackQueryHandler(cb_confirm,         pattern=r"^rpt_confirm\|"),
                CallbackQueryHandler(cb_edit,            pattern=r"^rpt_edit$"),
            ],
            ASK_EDIT_FIELD:    [
                CallbackQueryHandler(cb_edit_field,      pattern=r"^rpt_editfield\|"),
                CallbackQueryHandler(cb_edit_back,       pattern=r"^rpt_edit_back$"),
            ],
            ASK_EDIT_VALUE:    [
                MessageHandler(private_text,             recv_edit_value),
                CallbackQueryHandler(cb_edit_val_button, pattern=r"^rpt_editval\|"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_prefill,         pattern=r"^rpt_prefill\|"),
            CallbackQueryHandler(cb_loadtype,        pattern=r"^rpt_loadtype\|"),
            CallbackQueryHandler(cb_skip,            pattern=r"^rpt_skip$"),
            CallbackQueryHandler(cb_temp_recorder,   pattern=r"^rpt_temprec\|"),
            CallbackQueryHandler(cb_priority,        pattern=r"^rpt_priority\|"),
            CallbackQueryHandler(cb_confirm,         pattern=r"^rpt_confirm\|"),
            CallbackQueryHandler(cb_edit,            pattern=r"^rpt_edit$"),
            CallbackQueryHandler(cb_edit_field,      pattern=r"^rpt_editfield\|"),
            CallbackQueryHandler(cb_edit_back,       pattern=r"^rpt_edit_back$"),
            CallbackQueryHandler(cb_edit_val_button, pattern=r"^rpt_editval\|"),
        ],
        per_message=False,
        per_chat=False,
        per_user=True,
        allow_reentry=True,
    )
