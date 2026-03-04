"""
crash_reporter.py — Sends crash alerts to the Kurtex web server,
which then emails + Telegrams the admin.

Usage:
    from crash_reporter import send_crash_alert
    await send_crash_alert("main-bot", error, logs)
"""

import os
import logging
import traceback
import asyncio
import urllib.request
import json

logger = logging.getLogger(__name__)

WEB_URL      = os.getenv("KURTEX_WEB_URL", "")   # e.g. https://kurtex-web.up.railway.app
ALERT_SECRET = os.getenv("ALERT_SECRET", "")
BOT_NAME     = os.getenv("BOT_NAME", "kurtex-main-bot")


def send_crash_alert_sync(error: str, logs: str = "", level: str = "CRITICAL"):
    """Synchronous version — safe to call from exception handlers."""
    if not WEB_URL or not ALERT_SECRET:
        return
    try:
        payload = json.dumps({
            "bot":   BOT_NAME,
            "error": error,
            "logs":  logs,
            "level": level,
        }).encode()
        req = urllib.request.Request(
            f"{WEB_URL}/api/alert",
            data=payload,
            headers={
                "Content-Type":    "application/json",
                "x-alert-secret":  ALERT_SECRET,
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info("Crash alert sent")
    except Exception as e:
        logger.error(f"Failed to send crash alert: {e}")


def install_global_handler(bot_name: str = None):
    """
    Call once at startup to catch all unhandled exceptions
    and send crash alerts automatically.
    """
    import sys
    if bot_name:
        os.environ["BOT_NAME"] = bot_name

    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        error = f"{exc_type.__name__}: {exc_value}"
        logs  = "".join(traceback.format_tb(exc_tb))
        logger.critical(f"Unhandled exception: {error}")
        send_crash_alert_sync(error=error, logs=logs, level="CRITICAL")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = handle_exception
    logger.info(f"Crash reporter installed for {bot_name or BOT_NAME}")