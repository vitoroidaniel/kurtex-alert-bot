"""
handlers/scheduler.py
- Daily report at 06:50 America/New_York
- Escalation: first ping after 10 min, repeat every 30 min, max 5 rounds
"""

import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import Application

from config import config
from shifts import MAIN_ADMIN_ID, SUPER_ADMINS
from storage.case_store import mark_missed
from handlers.admin_handler import send_daily_report

logger = logging.getLogger(__name__)

ESCALATION_FIRST_MINUTES  = 10
ESCALATION_REPEAT_MINUTES = 30
ESCALATION_MAX_ROUNDS     = 5
ET = ZoneInfo("America/New_York")


async def job_daily_report(ctx) -> None:
    dest = config.REPORTS_GROUP_ID or next(iter(MAIN_ADMIN_ID), None)
    if not dest:
        logger.warning("No REPORTS_GROUP_ID — skipping daily report.")
        return
    await send_daily_report(ctx.bot, dest)


async def job_escalation_check(ctx) -> None:
    from shift_manager import get_all_admins

    alert_handler = ctx.bot_data.get("alert_handler")
    if not alert_handler:
        return

    now    = datetime.now(timezone.utc)
    first  = timedelta(minutes=ESCALATION_FIRST_MINUTES)
    repeat = timedelta(minutes=ESCALATION_REPEAT_MINUTES)

    for alert_id, record in list(alert_handler._alerts.items()):
        if record.get("taken_by"):
            continue

        created_at = record.get("created_at")
        if not created_at:
            continue
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        age = now - created_at
        if age < first:
            continue

        last_esc = record.get("last_escalated_at")
        if last_esc:
            if isinstance(last_esc, str):
                last_esc = datetime.fromisoformat(last_esc)
            if last_esc.tzinfo is None:
                last_esc = last_esc.replace(tzinfo=timezone.utc)
            if (now - last_esc) < repeat:
                continue

        count = record.get("escalation_count", 0)
        if count >= ESCALATION_MAX_ROUNDS:
            continue

        age_str     = f"{int(age.total_seconds() // 60)}m"
        short_id    = alert_handler._register_alert(alert_id)
        kb          = alert_handler._make_kb(short_id)
        group_name  = record.get("group_name", "Driver Group")
        driver_name = record.get("driver_name", "a driver")
        description = record.get("text", "")

        msg = (
            f"🔔 *Unassigned Alert — {age_str} old* (reminder {count + 1}/{ESCALATION_MAX_ROUNDS})\n\n"
            f"📌 *Group:* {group_name}\n"
            f"👤 *Driver:* {driver_name}\n"
            f"📝 {description[:200]}\n\n"
            "⚠️ *Please respond!*"
        )

        recipients = [{" id": aid} for aid in SUPER_ADMINS] if count >= ESCALATION_MAX_ROUNDS - 1 else get_all_admins()
        for admin in recipients:
            try:
                sent = await ctx.bot.send_message(
                    admin["id"], msg, parse_mode="Markdown", reply_markup=kb,
                )
                record["recipients"].setdefault(admin["id"], []).append(sent.message_id)
            except Exception as e:
                logger.warning(f"Escalation DM failed for {admin['id']}: {e}")

        record["last_escalated_at"] = now.isoformat()
        record["escalation_count"]  = count + 1

        if count == 0:
            mark_missed(alert_id)

        alert_handler._persist()
        logger.info(f"Alert {alert_id} escalation #{count + 1} after {age_str}")


def register_jobs(app: Application) -> None:
    jq = app.job_queue

    report_time = datetime.now(ET).replace(hour=6, minute=50, second=0, microsecond=0).timetz()
    jq.run_daily(job_daily_report, time=report_time, name="daily_report")
    jq.run_repeating(job_escalation_check, interval=300, first=60, name="escalation_check")

    logger.info("Jobs registered: daily_report @ 06:50 ET, escalation check every 5 min")
