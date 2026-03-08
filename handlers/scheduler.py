"""
handlers/scheduler.py

Scheduled background tasks:
  1. End-of-day report  — posted to REPORTS_GROUP_ID at 06:50 every day
  2. Escalation check   — every 5 mins, ping all admins if alert unassigned > X mins
"""

import logging
from datetime import datetime, timezone, timedelta

from telegram.ext import Application

from config import config
from shifts import MAIN_ADMIN_ID
from storage.case_store import mark_missed
from handlers.admin_handler import send_daily_report

logger = logging.getLogger(__name__)

ESCALATION_MINUTES = 10


async def job_daily_report(ctx) -> None:
    dest = config.REPORTS_GROUP_ID or MAIN_ADMIN_ID
    if not dest:
        logger.warning("No REPORTS_GROUP_ID or MAIN_ADMIN_ID set — skipping daily report.")
        return
    await send_daily_report(ctx.bot, dest)


async def job_escalation_check(ctx) -> None:
    from shift_manager import get_all_admins

    alert_handler = ctx.bot_data.get("alert_handler")
    if not alert_handler:
        return

    now       = datetime.now(timezone.utc)
    cutoff    = timedelta(minutes=ESCALATION_MINUTES)
    to_remove = []

    for alert_id, record in list(alert_handler._alerts.items()):
        if record.get("taken_by"):
            continue

        created_at = record.get("created_at")
        if not created_at:
            continue

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        age = now - created_at
        if age < cutoff:
            continue

        group_name  = record.get("group_name", "the driver group")
        driver_name = record.get("driver_name", "a driver")
        description = record.get("text", "")
        age_str     = f"{int(age.total_seconds() // 60)}m"

        msg = (
            f"\U0001f514 *Unassigned Alert — {age_str} old*\n\n"
            f"\U0001f4cc *Group:* {group_name}\n"
            f"\U0001f464 *Driver:* {driver_name}\n"
            f"\U0001f4dd {description[:200]}\n\n"
            "No one has taken this yet. Please respond!"
        )

        all_admins = get_all_admins()
        for admin in all_admins:
            try:
                await ctx.bot.send_message(admin["id"], msg, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Escalation DM failed for {admin['id']}: {e}")

        mark_missed(alert_id)
        to_remove.append(alert_id)
        logger.info(f"Alert {alert_id} escalated and marked missed after {age_str}")

    for alert_id in to_remove:
        alert_handler._alerts.pop(alert_id, None)


def register_jobs(app: Application) -> None:
    jq = app.job_queue

    jq.run_daily(
        job_daily_report,
        time=datetime.strptime("06:50", "%H:%M").time().replace(tzinfo=timezone.utc),
        name="daily_report",
    )

    jq.run_repeating(
        job_escalation_check,
        interval=300,
        first=60,
        name="escalation_check",
    )

    logger.info("Scheduled jobs registered: daily_report @ 06:50 UTC, escalation every 5min")
