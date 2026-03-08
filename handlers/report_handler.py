"""
handlers/report_handler.py
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CallbackQueryHandler, CommandHandler, MessageHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from config import config
from shift_manager import MAIN_ADMIN_ID

logger = logging.getLogger(__name__)

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
    ASK_MEDIA,
    ASK_PRIORITY,
    CONFIRM,
    ASK_EDIT_FIELD,
    ASK_EDIT_VALUE,
) = range(17)

SKIP_KB = InlineKeyboardMarkup([[InlineKeyboardButton("Skip", callback_data="rpt_skip")]])

LOAD_TYPE_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("JBS Load",    callback_data="rpt_loadtype|jbs"),
    InlineKeyboardButton("Broker Load", callback_data="rpt_loadtype|broker"),
    InlineKeyboardButton("Skip",        callback_data="rpt_skip"),
]])

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


def _type_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚛 Truck",   callback_data="rpt_type|truck"),
        InlineKeyboardButton("🚜 Trailer", callback_data="rpt_type|trailer"),
        InlineKeyboardButton("❄️ Reefer",  callback_data="rpt_type|reefer"),
    ]])


def _priority_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Low",    callback_data="rpt_priority|low"),
        InlineKeyboardButton("🟡 Medium", callback_data="rpt_priority|medium"),
        InlineKeyboardButton("🔴 High",   callback_data="rpt_priority|high"),
    ]])


def _confirm_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Send Report", callback_data="rpt_confirm|yes"),
            InlineKeyboardButton("❌ Cancel",      callback_data="rpt_confirm|no"),
        ],
        [
            InlineKeyboardButton("✏️ Edit",        callback_data="rpt_edit"),
        ],
    ])


def _esc(text) -> str:
    if not text or text == "—":
        return "—"
    for ch in ['_', '*', '`', '[']:
        text = str(text).replace(ch, f'\\{ch}')
    return text


def _build_report(d: dict) -> str:
    vtype        = d.get("vehicle_type", "truck")
    priority_key = d.get("priority", "low")
    p            = PRIORITY_META.get(priority_key, PRIORITY_META["low"])
    unit         = d.get("unit_number", "")

    if vtype == "truck":
        unit_line = f"*Truck:* {_esc(unit)}" if unit else "*Truck:* —"
    else:
        unit_line = f"*Trailer:* {_esc(unit)}" if unit else "*Trailer:* —"

    lines = [
        f"{p['icon']} *Case Report — {VTYPE_LABELS.get(vtype, vtype.title())}*",
        f"Priority: *{p['level']}*",
        "",
        unit_line,
        f"*Driver:* {_esc(d.get('driver', '—'))}",
        f"*Issue:* {_esc(d.get('issue', '—'))}",
        "",
        f"*JBS/Broker Load:* {_esc(d.get('load', '—'))}",
        f"*Pick up Location/Time:* {_esc(d.get('pickup', '—'))}",
        f"*Delivery Location/Time:* {_esc(d.get('delivery', '—'))}",
        f"*Current Location:* {_esc(d.get('location', '—'))}",
    ]

    if vtype in ("trailer", "reefer"):
        lines += [
            "",
            f"*Setpoint:* {_esc(d.get('setpoint', '—'))}",
            f"*Current temp:* {_esc(d.get('current_temp', '—'))}",
            f"*Temp recorder:* {_esc(d.get('temp_recorder', '—'))}",
        ]

    if d.get("comments"):
        lines += ["", f"*Comments:* {_esc(d.get('comments'))}"]

    lines += ["", f"*Handled by:* {_esc(d.get('handler', '—'))}"]
    return "\n".join(lines)


async def cb_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vtype = query.data.split("|")[1]
    ctx.user_data["report"] = {"media": [], "vehicle_type": vtype}
    unit_prompt = "Truck number:" if vtype == "truck" else "Trailer number:"
    label = {"truck": "🚛 Truck", "trailer": "🚜 Trailer", "reefer": "❄️ Reefer"}[vtype]
    await query.edit_message_text(
        f"Type: *{label}*\n\n{unit_prompt}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=None
    )
    return ASK_UNIT


async def recv_unit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["unit_number"] = update.message.text.strip()
    await update.message.reply_text("Driver name:")
    return ASK_DRIVER


async def recv_driver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["driver"] = update.message.text.strip()
    await update.message.reply_text("Issue description:")
    return ASK_ISSUE


async def recv_issue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["issue"] = update.message.text.strip()
    await update.message.reply_text("Load type:", reply_markup=LOAD_TYPE_KB)
    return ASK_LOAD


async def cb_loadtype(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ltype = query.data.split("|")[1]
    label = "JBS Load" if ltype == "jbs" else "Broker Load"
    ctx.user_data["report"]["load"] = label
    await query.edit_message_text(
        f"Load: *{label}*\n\nPick up Location / Time:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=SKIP_KB
    )
    return ASK_PICKUP


async def recv_load(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["load"] = update.message.text.strip()
    await update.message.reply_text("Pick up Location / Time:", reply_markup=SKIP_KB)
    return ASK_PICKUP


async def recv_pickup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["pickup"] = update.message.text.strip()
    await update.message.reply_text("Delivery Location / Time:", reply_markup=SKIP_KB)
    return ASK_DELIVERY


async def recv_delivery(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["delivery"] = update.message.text.strip()
    await update.message.reply_text("Current Location:", reply_markup=SKIP_KB)
    return ASK_LOCATION


async def recv_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["location"] = update.message.text.strip()
    vtype = ctx.user_data["report"].get("vehicle_type", "truck")
    if vtype in ("trailer", "reefer"):
        await update.message.reply_text("Setpoint temperature (e.g. -10°C):", reply_markup=SKIP_KB)
        return ASK_SETPOINT
    await update.message.reply_text("Comments:", reply_markup=SKIP_KB)
    return ASK_COMMENTS


async def recv_setpoint(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["setpoint"] = update.message.text.strip()
    await update.message.reply_text("Current temperature:", reply_markup=SKIP_KB)
    return ASK_CURRENT_TEMP


async def recv_current_temp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["current_temp"] = update.message.text.strip()
    await update.message.reply_text(
        "Temp recorder: Y or N?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Y", callback_data="rpt_temprec|Y"),
            InlineKeyboardButton("N", callback_data="rpt_temprec|N"),
        ]])
    )
    return ASK_TEMP_RECORDER


async def cb_temp_recorder(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["report"]["temp_recorder"] = query.data.split("|")[1]
    await query.edit_message_text("Comments:", reply_markup=None)
    return ASK_COMMENTS


async def recv_comments(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["comments"] = update.message.text.strip()
    await update.message.reply_text(
        "Send photo(s) or video(s). Press Done when finished:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Done (no media)", callback_data="rpt_mediadone")
        ]])
    )
    return ASK_MEDIA


async def recv_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg    = update.message
    report = ctx.user_data.setdefault("report", {"media": []})
    media  = report.setdefault("media", [])
    try:
        if msg.photo:
            file_id = msg.photo[-1].file_id
            if not any(fid == file_id for _, fid in media):
                media.append(("photo", file_id))
            kind = "Photo"
        elif msg.video:
            media.append(("video", msg.video.file_id))
            kind = "Video"
        elif msg.document:
            media.append(("document", msg.document.file_id))
            kind = "File"
        else:
            await msg.reply_text("Please send a photo, video, or file.")
            return ASK_MEDIA
        await msg.reply_text(
            f"{kind} received ({len(media)} total). Send more or press Done:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Done ✅", callback_data="rpt_mediadone")]])
        )
    except Exception as e:
        logger.error(f"recv_media error: {e}")
        await msg.reply_text(
            "Something went wrong. Try again or press Done:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Done ✅", callback_data="rpt_mediadone")]])
        )
    return ASK_MEDIA


async def cb_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    report = ctx.user_data.get("report", {})

    if "load" not in report:
        report["load"] = "—"
        await query.edit_message_text("Pick up Location / Time:", reply_markup=SKIP_KB)
        return ASK_PICKUP
    elif "pickup" not in report:
        report["pickup"] = "—"
        await query.edit_message_text("Delivery Location / Time:", reply_markup=SKIP_KB)
        return ASK_DELIVERY
    elif "delivery" not in report:
        report["delivery"] = "—"
        await query.edit_message_text("Current Location:", reply_markup=SKIP_KB)
        return ASK_LOCATION
    elif "location" not in report:
        report["location"] = "—"
        vtype = report.get("vehicle_type", "truck")
        if vtype in ("trailer", "reefer"):
            await query.edit_message_text("Setpoint temperature:", reply_markup=SKIP_KB)
            return ASK_SETPOINT
        report["comments"] = None
        await query.edit_message_text(
            "Send photo(s) or video(s), or press Done:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Done (no media)", callback_data="rpt_mediadone")]])
        )
        return ASK_MEDIA
    elif "setpoint" not in report:
        report["setpoint"] = "—"
        await query.edit_message_text("Current temperature:", reply_markup=SKIP_KB)
        return ASK_CURRENT_TEMP
    elif "current_temp" not in report:
        report["current_temp"] = "—"
        await query.edit_message_text(
            "Temp recorder: Y or N?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Y", callback_data="rpt_temprec|Y"),
                InlineKeyboardButton("N", callback_data="rpt_temprec|N"),
            ]])
        )
        return ASK_TEMP_RECORDER
    elif "comments" not in report:
        report["comments"] = None
        await query.edit_message_text(
            "Send photo(s) or video(s), or press Done:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Done (no media)", callback_data="rpt_mediadone")]])
        )
        return ASK_MEDIA
    return ASK_COMMENTS


async def cb_media_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query       = update.callback_query
    await query.answer()
    vtype       = ctx.user_data.get("report", {}).get("vehicle_type", "truck")
    vtype_label = VTYPE_LABELS.get(vtype, vtype.title())
    await query.edit_message_text(
        f"Vehicle: *{vtype_label}*\n\nSelect priority:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_priority_kb()
    )
    return ASK_PRIORITY


async def cb_priority(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    ctx.user_data["report"]["priority"] = query.data.split("|")[1]
    preview = _build_report(ctx.user_data["report"])
    media   = ctx.user_data["report"].get("media", [])
    note    = f"\n\n📎 {len(media)} media file(s) attached" if media else ""
    await query.edit_message_text(
        f"*Preview — confirm and send?*\n\n{preview}{note}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_confirm_kb()
    )
    return CONFIRM


async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    action = query.data.split("|")[1]

    if action == "no":
        ctx.user_data.pop("report", None)
        await query.edit_message_text("Report cancelled.", reply_markup=None)
        return ConversationHandler.END

    data    = ctx.user_data.pop("report", {})
    dest_id = config.REPORTS_GROUP_ID or MAIN_ADMIN_ID
    if not dest_id:
        await query.edit_message_text("No reports group configured.", reply_markup=None)
        return ConversationHandler.END

    report_text = _build_report(data)
    media       = data.get("media", [])

    try:
        if media:
            kind, file_id = media[0]
            try:
                if kind == "photo":
                    await ctx.bot.send_photo(dest_id, photo=file_id, caption=report_text, parse_mode=ParseMode.MARKDOWN, read_timeout=30, write_timeout=30)
                elif kind == "video":
                    await ctx.bot.send_video(dest_id, video=file_id, caption=report_text, parse_mode=ParseMode.MARKDOWN, read_timeout=60, write_timeout=60)
                else:
                    await ctx.bot.send_document(dest_id, document=file_id, caption=report_text, parse_mode=ParseMode.MARKDOWN, read_timeout=30, write_timeout=30)
            except TelegramError as e:
                logger.error(f"First media failed: {e}")
                await ctx.bot.send_message(dest_id, report_text, parse_mode=ParseMode.MARKDOWN)
            for kind, file_id in media[1:]:
                try:
                    if kind == "photo":   await ctx.bot.send_photo(dest_id, photo=file_id, read_timeout=30, write_timeout=30)
                    elif kind == "video": await ctx.bot.send_video(dest_id, video=file_id, read_timeout=60, write_timeout=60)
                    else:                 await ctx.bot.send_document(dest_id, document=file_id, read_timeout=30, write_timeout=30)
                except TelegramError as e:
                    logger.error(f"Media item failed: {e}")
        else:
            await ctx.bot.send_message(dest_id, report_text, parse_mode=ParseMode.MARKDOWN)

        await query.edit_message_text("✅ Report sent!", reply_markup=None)
        logger.info(f"Report sent to {dest_id}")

    except TelegramError as e:
        logger.error(f"Failed to send report: {e}")
        await query.edit_message_text("Failed to send report. Please try again.", reply_markup=None)

    return ConversationHandler.END



async def _show_preview(query_or_msg, ctx, edit=True):
    """Re-show the preview. Works from both callback queries and messages."""
    report  = ctx.user_data.get("report", {})
    preview = _build_report(report)
    media   = report.get("media", [])
    note    = f"\n\n📎 {len(media)} media file(s) attached" if media else ""
    text    = f"*Preview — confirm and send?*\n\n{preview}{note}"
    if edit and hasattr(query_or_msg, "edit_message_text"):
        await query_or_msg.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=_confirm_kb())
    else:
        target = query_or_msg.message if hasattr(query_or_msg, "message") else query_or_msg
        await target.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=_confirm_kb())


def _edit_fields_kb(vtype: str) -> InlineKeyboardMarkup:
    """Keyboard listing all editable fields for the current vehicle type."""
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
    if vtype in ("trailer", "reefer"):
        fields[7:7] = [
            ("Setpoint",      "rpt_editfield|setpoint"),
            ("Current temp",  "rpt_editfield|current_temp"),
            ("Temp recorder", "rpt_editfield|temp_recorder"),
        ]
    rows = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in fields]
    rows.append([InlineKeyboardButton("↩️ Back to preview", callback_data="rpt_edit_back")])
    return InlineKeyboardMarkup(rows)


async def cb_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show the list of fields agent can edit."""
    query = update.callback_query
    await query.answer()
    vtype = ctx.user_data.get("report", {}).get("vehicle_type", "truck")
    await query.edit_message_text(
        "Which field would you like to edit?",
        reply_markup=_edit_fields_kb(vtype)
    )
    return ASK_EDIT_FIELD


async def cb_edit_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Go back to preview without changing anything."""
    query = update.callback_query
    await query.answer()
    await _show_preview(query, ctx, edit=True)
    return CONFIRM


async def cb_edit_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Agent picked a field — ask for new value."""
    query = update.callback_query
    await query.answer()
    field = query.data.split("|")[1]
    ctx.user_data["editing_field"] = field

    prompts = {
        "unit":         "Enter new unit number:",
        "driver":       "Enter new driver name:",
        "issue":        "Enter new issue description:",
        "load":         "Enter load type (JBS Load / Broker Load):",
        "pickup":       "Enter new pick up location/time:",
        "delivery":     "Enter new delivery location/time:",
        "location":     "Enter new current location:",
        "setpoint":     "Enter new setpoint temperature:",
        "current_temp": "Enter new current temperature:",
        "temp_recorder":"Temp recorder: Y or N?",
        "comments":     "Enter new comments:",
        "priority":     "Select new priority:",
    }

    if field == "temp_recorder":
        await query.edit_message_text(
            prompts[field],
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Y", callback_data="rpt_editval|Y"),
                InlineKeyboardButton("N", callback_data="rpt_editval|N"),
            ]])
        )
        return ASK_EDIT_VALUE

    if field == "priority":
        await query.edit_message_text(
            prompts[field],
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🟢 Low",    callback_data="rpt_editval|low"),
                InlineKeyboardButton("🟡 Medium", callback_data="rpt_editval|medium"),
                InlineKeyboardButton("🔴 High",   callback_data="rpt_editval|high"),
            ]])
        )
        return ASK_EDIT_VALUE

    await query.edit_message_text(prompts.get(field, "Enter new value:"))
    return ASK_EDIT_VALUE


async def cb_edit_val_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle button-based edit values (priority, temp_recorder)."""
    query = update.callback_query
    await query.answer()
    field = ctx.user_data.pop("editing_field", None)
    value = query.data.split("|")[1]
    if field:
        field_map = {"unit": "unit_number"}
        ctx.user_data["report"][field_map.get(field, field)] = value
    await _show_preview(query, ctx, edit=True)
    return CONFIRM


async def recv_edit_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle text-based edit values."""
    value = update.message.text.strip()
    field = ctx.user_data.pop("editing_field", None)
    if field:
        field_map = {"unit": "unit_number"}
        ctx.user_data["report"][field_map.get(field, field)] = value
    await _show_preview(update.message, ctx, edit=False)
    return CONFIRM

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("report", None)
    await update.message.reply_text("Report cancelled. Use /mycases to continue.")
    return ConversationHandler.END


def get_report_conversation():
    text_only    = filters.TEXT & ~filters.COMMAND
    media_filter = filters.PHOTO | filters.VIDEO | filters.Document.ALL

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_type, pattern=r'^rpt_type\|')],
        states={
            ASK_TYPE:          [CallbackQueryHandler(cb_type,          pattern=r'^rpt_type\|')],
            ASK_UNIT:          [MessageHandler(text_only,              recv_unit)],
            ASK_DRIVER:        [MessageHandler(text_only,              recv_driver)],
            ASK_ISSUE:         [MessageHandler(text_only,              recv_issue)],
            ASK_LOAD:          [
                                CallbackQueryHandler(cb_loadtype,      pattern=r'^rpt_loadtype\|'),
                                CallbackQueryHandler(cb_skip,          pattern=r'^rpt_skip$'),
                                MessageHandler(text_only,              recv_load),
                               ],
            ASK_PICKUP:        [MessageHandler(text_only,              recv_pickup),
                                CallbackQueryHandler(cb_skip,          pattern=r'^rpt_skip$')],
            ASK_DELIVERY:      [MessageHandler(text_only,              recv_delivery),
                                CallbackQueryHandler(cb_skip,          pattern=r'^rpt_skip$')],
            ASK_LOCATION:      [MessageHandler(text_only,              recv_location),
                                CallbackQueryHandler(cb_skip,          pattern=r'^rpt_skip$')],
            ASK_SETPOINT:      [MessageHandler(text_only,              recv_setpoint),
                                CallbackQueryHandler(cb_skip,          pattern=r'^rpt_skip$')],
            ASK_CURRENT_TEMP:  [MessageHandler(text_only,              recv_current_temp),
                                CallbackQueryHandler(cb_skip,          pattern=r'^rpt_skip$')],
            ASK_TEMP_RECORDER: [CallbackQueryHandler(cb_temp_recorder, pattern=r'^rpt_temprec\|')],
            ASK_COMMENTS:      [MessageHandler(text_only,              recv_comments),
                                CallbackQueryHandler(cb_skip,          pattern=r'^rpt_skip$')],
            ASK_MEDIA:         [MessageHandler(media_filter,           recv_media),
                                CallbackQueryHandler(cb_media_done,    pattern=r'^rpt_mediadone$')],
            ASK_PRIORITY:      [CallbackQueryHandler(cb_priority,      pattern=r'^rpt_priority\|')],
            CONFIRM:           [
                                CallbackQueryHandler(cb_confirm,       pattern=r'^rpt_confirm\|'),
                                CallbackQueryHandler(cb_edit,          pattern=r'^rpt_edit$'),
                               ],
            ASK_EDIT_FIELD:    [
                                CallbackQueryHandler(cb_edit_field,    pattern=r'^rpt_editfield\|'),
                                CallbackQueryHandler(cb_edit_back,     pattern=r'^rpt_edit_back$'),
                               ],
            ASK_EDIT_VALUE:    [
                                MessageHandler(text_only,              recv_edit_value),
                                CallbackQueryHandler(cb_edit_val_button, pattern=r'^rpt_editval\|'),
                               ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
        per_chat=False,
        per_user=True,
        allow_reentry=True,
    )
