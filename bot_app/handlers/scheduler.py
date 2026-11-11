"""handlers/scheduler.py -- DST-safe daily report and parallel async escalations."""

from __future__ import annotations

import asyncio
import logging
from datetime import time, timedelta

from telegram.ext import Application

from backend.core.app_time import MOLDOVA_TZ, MOLDOVA_TIMEZONE_LABEL, parse_timestamp, utc_now
from backend.core.config import config
from backend.core.shift_manager import get_all_admins
from backend.storage.case_store import async_mark_missed, async_upsert_active_alert
from backend.storage.user_store import get_all_user_dicts
from bot_app.handlers.admin_handler import send_daily_report
from bot_app.telegram_utils import esc_html, safe_send_message

logger = logging.getLogger(__name__)

ESCALATION_FIRST_MINUTES = 10
ESCALATION_REPEAT_MINUTES = 10
ESCALATION_MAX_ROUNDS = 5


async def job_daily_report(ctx) -> None:
    dest = config.REPORTS_GROUP_ID
    if not dest:
        logger.warning("No REPORTS_GROUP_ID -- skipping daily report.")
        return
    await send_daily_report(ctx.bot, dest)


async def job_escalation_check(ctx) -> None:
    alert_handler = ctx.bot_data.get("alert_handler")
    if not alert_handler:
        return

    now = utc_now()
    first = timedelta(minutes=ESCALATION_FIRST_MINUTES)
    repeat = timedelta(minutes=ESCALATION_REPEAT_MINUTES)

    with alert_handler._state_lock:
        snapshot_items = list(alert_handler._alerts.items())

    for alert_id, record in snapshot_items:
        if record.get("taken_by"):
            continue

        created_at = parse_timestamp(record.get("created_at"))
        if not created_at:
            continue

        age = now - created_at
        if age < first:
            continue

        last_esc = record.get("last_escalated_at")
        if last_esc:
            last_esc_dt = parse_timestamp(last_esc)
            if not last_esc_dt or (now - last_esc_dt) < repeat:
                continue

        count = record.get("escalation_count", 0)
        if count >= ESCALATION_MAX_ROUNDS:
            continue

        age_str = f"{int(age.total_seconds() // 60)}m"
        short_id = alert_handler._register_alert(alert_id)
        kb = alert_handler._make_kb(short_id)
        group_name = record.get("group_name", "Driver Group")
        driver_name = record.get("driver_name", "a driver")
        description = (record.get("text") or "")[:200]

        msg = (
            f"🔔 <b>Unassigned Alert — {esc_html(age_str)} old</b> "
            f"(reminder {count + 1}/{ESCALATION_MAX_ROUNDS})\n\n"
            f"📌 <b>Group:</b> {esc_html(group_name)}\n"
            f"👤 <b>Driver:</b> {esc_html(driver_name)}\n"
            f"📝 {esc_html(description)}\n\n"
            "⚠️ <b>Please respond!</b>"
        )

        if count >= ESCALATION_MAX_ROUNDS - 1:
            recipients = await asyncio.to_thread(
                lambda: [u for u in get_all_user_dicts() if u.get("role") == "super_admin"]
            )
        else:
            recipients = await asyncio.to_thread(get_all_admins)

        async def _send_esc(admin):
            try:
                sent = await safe_send_message(ctx.bot, admin["id"], msg, reply_markup=kb)
                if sent:
                    record["recipients"].setdefault(admin["id"], []).append(sent.message_id)
            except Exception as exc:
                logger.warning("Escalation DM failed for %s: %s", admin["id"], exc)

        if recipients:
            await asyncio.gather(*(_send_esc(a) for a in recipients), return_exceptions=True)

        record["last_escalated_at"] = now.isoformat()
        record["escalation_count"] = count + 1

        if count == 0:
            await async_mark_missed(alert_id)

        await async_upsert_active_alert(alert_id, record)
        logger.info("Alert %s escalation #%d after %s", alert_id, count + 1, age_str)


def register_jobs(app: Application) -> None:
    jq = app.job_queue
    # Pass ZoneInfo directly so APScheduler respects Europe/Chisinau DST changes.
    report_time = time(hour=14, minute=30, second=0, tzinfo=MOLDOVA_TZ)
    jq.run_daily(job_daily_report, time=report_time, name="daily_report")
    jq.run_repeating(job_escalation_check, interval=30, first=60, name="escalation_check")
    logger.info("Jobs registered: daily_report @ 14:30 %s, escalation check every 30s", MOLDOVA_TIMEZONE_LABEL)
