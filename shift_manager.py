"""
shift_manager.py - Returns which admins are currently on shift.
Now reads users dynamically from user_store instead of hardcoded shifts.py.
"""

from datetime import datetime, time
import zoneinfo
from shifts import SHIFTS, TIMEZONE, MAIN_ADMIN_ID


def _get_all_alert_users() -> list[dict]:
    """Pull alert-eligible users (super_admin + agent) from dynamic store."""
    from storage.user_store import get_all_user_dicts
    return [u for u in get_all_user_dicts() if u["role"] in ("super_admin", "agent")]


def get_on_shift_admins() -> list[dict]:
    """Returns list of alert-eligible users currently on shift."""
    try:
        tz = zoneinfo.ZoneInfo(TIMEZONE)
    except Exception:
        tz = zoneinfo.ZoneInfo("America/New_York")

    now      = datetime.now(tz)
    weekday  = now.weekday()
    now_time = now.time().replace(second=0, microsecond=0)

    in_shift = False
    for shift in SHIFTS:
        if weekday not in shift["days"]:
            continue
        s = shift["start"]
        e = shift["end"]
        if s <= e:
            if s <= now_time < e:
                in_shift = True
                break
        else:
            if now_time >= s or now_time < e:
                in_shift = True
                break

    if not in_shift:
        return []

    return _get_all_alert_users()


def get_all_admins() -> list[dict]:
    """Returns all alert-eligible users regardless of shift (fallback)."""
    return _get_all_alert_users()


def get_current_shift_name() -> str:
    try:
        tz = zoneinfo.ZoneInfo(TIMEZONE)
    except Exception:
        tz = zoneinfo.ZoneInfo("America/New_York")

    now      = datetime.now(tz)
    weekday  = now.weekday()
    now_time = now.time().replace(second=0, microsecond=0)

    for shift in SHIFTS:
        if weekday not in shift["days"]:
            continue
        s, e = shift["start"], shift["end"]
        if s <= e:
            if s <= now_time < e:
                return shift["name"]
        else:
            if now_time >= s or now_time < e:
                return shift["name"]

    return "Off Hours"
