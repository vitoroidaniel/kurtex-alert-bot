"""Shared timezone helpers for the bot and dashboard."""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

CENTRAL_TIMEZONE_NAME = "America/Chicago"
CENTRAL_TZ = ZoneInfo(CENTRAL_TIMEZONE_NAME)
CENTRAL_TIMEZONE_LABEL = "CT"


def utc_now() -> datetime:
    """Return an aware UTC timestamp for storage and elapsed-time math."""
    return datetime.now(timezone.utc)


def chicago_now() -> datetime:
    """Return the current application time in America/Chicago."""
    return datetime.now(CENTRAL_TZ)


def parse_timestamp(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def chicago_timestamp(value) -> datetime | None:
    dt = parse_timestamp(value)
    return dt.astimezone(CENTRAL_TZ) if dt else None


def chicago_date_str(value) -> str:
    dt = chicago_timestamp(value)
    return dt.date().isoformat() if dt else ""


def today_str() -> str:
    return chicago_now().date().isoformat()


def week_start_str() -> str:
    now = chicago_now()
    return (now - timedelta(days=now.weekday())).date().isoformat()


def month_start_str() -> str:
    return chicago_now().date().replace(day=1).isoformat()
