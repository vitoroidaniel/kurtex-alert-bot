"""
handlers/scheduler.py

Scheduled background tasks:
  1. End-of-day report  — posted to REPORTS_GROUP_ID at 23:55 every day
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

# How many minutes before an unassigned alert gets escalated
ESCALATION_MINUTES = 10


# ── End-of-day report ─────────────────────────────────────────────────────────

async def job_daily_report(ctx) -> None:
    """Runs at 23:55 — sends report to reports group or main admin."""
    dest = config.REPORTS_GROUP_ID or MAIN_ADMIN_ID
    if not dest:
        logger.warning("No REPORTS_GROUP_ID or MAIN_ADMIN_ID set — skipping daily report.")
        return
    await send_daily_report(ctx.bot, dest)


# ── Escalation check ──────────────────────────────────────────────────────────

async def job_escalation_check(ctx) -> None:
    """
    Runs every 5 minutes.
    Checks the alert_handler's active alerts for any that have been
    unassigned for longer than ESCALATION_MINUTES.
    Pings all admins and marks them as missed in case_store.
    """
    from handlers.alert_handler import AlertHandler
    from shift_manager import get_all_admins
    from storage.case_store import get_case

    # Access the shared AlertHandler instance stored in bot_data
    alert_handler = ctx.bot_data.get("alert_handler")
    if not alert_handler:
        return

    now       = datetime.now(timezone.utc)
    cutoff    = timedelta(minutes=ESCALATION_MINUTES)
    to_remove = []

    for alert_id, record in alert_handler._alerts.items():
        if record.get("taken_by"):
            continue  # already assigned

        created_at = record.get("created_at")
        if not created_at:
            continue

        # created_at is naive datetime — make it UTC-aware
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        age = now - created_at
        if age < cutoff:
            continue

        # This alert has been unassigned too long — escalate
        group_name   = record.get("group_name", "the driver group")
        driver_name  = record.get("driver_name", "a driver")
        description  = record.get("text", "")
        age_str      = f"{int(age.total_seconds() // 60)}m"

        msg = (
            f"🔔 *Unassigned Alert — {age_str} old*\n\n"
            f"📌 *Group:* {group_name}\n"
            f"👤 *Driver:* {driver_name}\n"
            f"📝 {description[:200]}\n\n"
            "No one has taken this yet. Please respond!"
        )

        all_admins = get_all_admins()
        for admin in all_admins:
            try:
                await ctx.bot.send_message(
                    admin["id"], msg,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Escalation DM failed for {admin['id']}: {e}")

        # Mark as missed in case_store and remove from active alerts
        mark_missed(alert_id)
        to_remove.append(alert_id)
        logger.info(f"Alert {alert_id} escalated and marked missed after {age_str}")

    for alert_id in to_remove:
        alert_handler._alerts.pop(alert_id, None)


# ── Register jobs with the application ───────────────────────────────────────

def register_jobs(app: Application) -> None:
    from config import config
    
    jq = app.job_queue

    # Daily report at 23:55
    jq.run_daily(
        job_daily_report,
        time=datetime.strptime("06:50", "%H:%M").time().replace(tzinfo=timezone.utc),
        name="daily_report",
    )

    # Escalation check every 5 minutes
    jq.run_repeating(
        job_escalation_check,
        interval=300,   # seconds
        first=60,       # start after 60s so bot is fully ready
        name="escalation_check",
    )

    # AI Alerts channel polling - every 10 seconds if configured
    if getattr(config, "AI_ALERTS_CHANNEL_ID", 0):
        async def job_poll_ai(ctx):
            alert_handler = ctx.bot_data.get("alert_handler")
            if alert_handler:
                await alert_handler.poll_ai_alerts(ctx)
        
        jq.run_repeating(
            job_poll_ai,
            interval=10,   # 10 seconds
            first=30,      # start after 30s so bot is fully ready
            name="poll_ai_alerts",
        )
        logger.info(f"AI Alerts polling enabled (channel ID: {config.AI_ALERTS_CHANNEL_ID})")
    else:
        logger.info("AI Alerts channel not configured - polling disabled")

    logger.info("Scheduled jobs registered: daily_report @ 23:55 UTC, escalation every 5min, AI poll every 10s")
