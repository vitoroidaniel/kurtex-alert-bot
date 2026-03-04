"""
handlers/report_handler.py

Report flow triggered by "Assign & Report" button.

Template:
  Truck/Trailer:
  Driver:
  Issue:
  JBS/Broker Load:
  Pick up Location/Time:
  Delivery Location/Time:
  Current Location:
  (If reefer) Setpoint / Current temp / Temp recorder
  Comments:
  Photo/Video:

Priority colors:
  🟢 Truck
  🟡 Trailer
  🔴 Reefer issue
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

# ── States ────────────────────────────────────────────────────────────────────
(
    ASK_TYPE,
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
) = range(14)

SKIP_KB = InlineKeyboardMarkup([[InlineKeyboardButton("Skip", callback_data="rpt_skip")]])


def _type_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚛 Truck",   callback_data="rpt_type|truck"),
        InlineKeyboardButton("🚜 Trailer", callback_data="rpt_type|trailer"),
        InlineKeyboardButton("❄️ Reefer",  callback_data="rpt_type|reefer"),
    ]])


def _priority_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Truck (Low)",      callback_data="rpt_priority|truck"),
        InlineKeyboardButton("🟡 Trailer (Medium)", callback_data="rpt_priority|trailer"),
        InlineKeyboardButton("🔴 Reefer (High)",    callback_data="rpt_priority|reefer"),
    ]])


def _confirm_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Send Report", callback_data="rpt_confirm|yes"),
        InlineKeyboardButton("❌ Cancel",      callback_data="rpt_confirm|no"),
    ]])


PRIORITY_META = {
    "truck":   {"icon": "🟢", "label": "Truck",          "level": "Low"},
    "trailer": {"icon": "🟡", "label": "Trailer",         "level": "Medium"},
    "reefer":  {"icon": "🔴", "label": "Reefer Issue",    "level": "High"},
}


def _build_report(d: dict) -> str:
    vtype = d.get("vehicle_type", "truck")
    p     = PRIORITY_META.get(vtype, PRIORITY_META["truck"])

    lines = [
        f"{p['icon']} *Case Report — {p['label']}*",
        f"Priority: *{p['level']}*",
        "",
        f"*Truck/Trailer:* {d.get('vehicle_type', '—').title()}",
        f"*Driver:* {d.get('driver', '—')}",
        f"*Issue:* {d.get('issue', '—')}",
        "",
        f"*JBS/Broker Load:* {d.get('load', '—')}",
        f"*Pick up Location/Time:* {d.get('pickup', '—')}",
        f"*Delivery Location/Time:* {d.get('delivery', '—')}",
        f"*Current Location:* {d.get('location', '—')}",
    ]

    if vtype in ("trailer", "reefer"):
        lines += [
            "",
            f"*Setpoint:* {d.get('setpoint', '—')}",
            f"*Current temp:* {d.get('current_temp', '—')}",
            f"*Temp recorder:* {d.get('temp_recorder', '—')}",
        ]

    comments = d.get("comments")
    if comments:
        lines += ["", f"*Comments:* {comments}"]

    handler = d.get("handler", "—")
    lines  += ["", f"*Handled by:* {handler}"]

    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _ask(update_or_query, text, reply_markup=None, edit=False):
    """Send or edit a message."""
    if edit and hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
        )
    elif hasattr(update_or_query, "message"):
        await update_or_query.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
        )
    else:
        await update_or_query.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
        )


# ── Step handlers ─────────────────────────────────────────────────────────────

async def cb_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vtype = query.data.split("|")[1]

    if "report" not in ctx.user_data:
        ctx.user_data["report"] = {"media": []}

    ctx.user_data["report"]["vehicle_type"] = vtype
    label = {"truck": "🚛 Truck", "trailer": "🚜 Trailer", "reefer": "❄️ Reefer"}[vtype]

    await query.edit_message_text(
        f"Type: *{label}*\n\nDriver name:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=None
    )
    return ASK_DRIVER


async def recv_driver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["driver"] = update.message.text.strip()
    await update.message.reply_text("Issue description:")
    return ASK_ISSUE


async def recv_issue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["report"]["issue"] = update.message.text.strip()
    await update.message.reply_text("JBS/Broker Load #:", reply_markup=SKIP_KB)
    return ASK_LOAD


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
    """Handle incoming photo, video, or document."""
    msg    = update.message
    report = ctx.user_data.setdefault("report", {"media": []})
    media  = report.setdefault("media", [])

    if msg.photo:
        media.append(("photo", msg.photo[-1].file_id))
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

    count = len(media)
    await msg.reply_text(
        f"{kind} received ({count} total).\nSend more or press Done:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Done", callback_data="rpt_mediadone")
        ]])
    )
    return ASK_MEDIA


# ── Skip handler ──────────────────────────────────────────────────────────────

async def cb_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Generic skip — figure out which step we're in by what's missing."""
    query = update.callback_query
    await query.answer()
    report = ctx.user_data.get("report", {})

    # Determine next missing field
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
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Done (no media)", callback_data="rpt_mediadone")
            ]])
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
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Done (no media)", callback_data="rpt_mediadone")
            ]])
        )
        return ASK_MEDIA

    return ASK_COMMENTS


async def cb_media_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    vtype  = ctx.user_data.get("report", {}).get("vehicle_type", "truck")
    p      = PRIORITY_META.get(vtype, PRIORITY_META["truck"])

    await query.edit_message_text(
        f"Suggested priority: *{p['icon']} {p['label']} ({p['level']})*\n\nConfirm or override:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_priority_kb()
    )
    return ASK_PRIORITY


async def cb_priority(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    chosen = query.data.split("|")[1]
    ctx.user_data["report"]["vehicle_type"] = chosen

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
            # Send first item with caption
            kind, file_id = media[0]
            if kind == "photo":
                await ctx.bot.send_photo(
                    dest_id, photo=file_id,
                    caption=report_text, parse_mode=ParseMode.MARKDOWN
                )
            elif kind == "video":
                await ctx.bot.send_video(
                    dest_id, video=file_id,
                    caption=report_text, parse_mode=ParseMode.MARKDOWN
                )
            else:
                await ctx.bot.send_document(
                    dest_id, document=file_id,
                    caption=report_text, parse_mode=ParseMode.MARKDOWN
                )

            # Remaining media without caption
            for kind, file_id in media[1:]:
                if kind == "photo":
                    await ctx.bot.send_photo(dest_id, photo=file_id)
                elif kind == "video":
                    await ctx.bot.send_video(dest_id, video=file_id)
                else:
                    await ctx.bot.send_document(dest_id, document=file_id)
        else:
            await ctx.bot.send_message(
                dest_id, report_text, parse_mode=ParseMode.MARKDOWN
            )

        await query.edit_message_text("✅ Report sent!", reply_markup=None)
        logger.info(f"Report sent to {dest_id}")

    except TelegramError as e:
        logger.error(f"Failed to send report: {e}")
        await query.edit_message_text(f"Failed to send: {e}", reply_markup=None)

    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("report", None)
    await update.message.reply_text("Report cancelled. Use /mycases to continue.")
    return ConversationHandler.END


# ── Exported conversation handler ─────────────────────────────────────────────

def get_report_conversation():
    text_only = filters.TEXT & ~filters.COMMAND
    media_filter = filters.PHOTO | filters.VIDEO | filters.Document.ALL

    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_type, pattern=r'^rpt_type\|'),
        ],
        states={
            ASK_TYPE:         [CallbackQueryHandler(cb_type,         pattern=r'^rpt_type\|')],
            ASK_DRIVER:       [MessageHandler(text_only,             recv_driver)],
            ASK_ISSUE:        [MessageHandler(text_only,             recv_issue)],
            ASK_LOAD:         [
                MessageHandler(text_only,                            recv_load),
                CallbackQueryHandler(cb_skip,                        pattern=r'^rpt_skip$'),
            ],
            ASK_PICKUP:       [
                MessageHandler(text_only,                            recv_pickup),
                CallbackQueryHandler(cb_skip,                        pattern=r'^rpt_skip$'),
            ],
            ASK_DELIVERY:     [
                MessageHandler(text_only,                            recv_delivery),
                CallbackQueryHandler(cb_skip,                        pattern=r'^rpt_skip$'),
            ],
            ASK_LOCATION:     [
                MessageHandler(text_only,                            recv_location),
                CallbackQueryHandler(cb_skip,                        pattern=r'^rpt_skip$'),
            ],
            ASK_SETPOINT:     [
                MessageHandler(text_only,                            recv_setpoint),
                CallbackQueryHandler(cb_skip,                        pattern=r'^rpt_skip$'),
            ],
            ASK_CURRENT_TEMP: [
                MessageHandler(text_only,                            recv_current_temp),
                CallbackQueryHandler(cb_skip,                        pattern=r'^rpt_skip$'),
            ],
            ASK_TEMP_RECORDER:[
                CallbackQueryHandler(cb_temp_recorder,               pattern=r'^rpt_temprec\|'),
            ],
            ASK_COMMENTS:     [
                MessageHandler(text_only,                            recv_comments),
                CallbackQueryHandler(cb_skip,                        pattern=r'^rpt_skip$'),
            ],
            ASK_MEDIA:        [
                MessageHandler(media_filter,                         recv_media),
                CallbackQueryHandler(cb_media_done,                  pattern=r'^rpt_mediadone$'),
            ],
            ASK_PRIORITY:     [CallbackQueryHandler(cb_priority,     pattern=r'^rpt_priority\|')],
            CONFIRM:          [CallbackQueryHandler(cb_confirm,      pattern=r'^rpt_confirm\|')],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
        per_chat=False,
        per_user=True,
        allow_reentry=True,
    )