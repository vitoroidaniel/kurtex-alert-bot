"""
shift_manager.py - Returns which admins are currently on shift.
"""

from datetime import datetime, time
import zoneinfo
from shifts import ADMINS, SHIFTS, TIMEZONE, MAIN_ADMIN_ID


def get_on_shift_admins() -> list[dict]:
    """Returns list of admins currently on shift."""
    try:
        tz = zoneinfo.ZoneInfo(TIMEZONE)
    except Exception:
        tz = zoneinfo.ZoneInfo("America/New_York")

    now      = datetime.now(tz)
    weekday  = now.weekday()   # 0=Mon, 6=Sun
    now_time = now.time().replace(second=0, microsecond=0)

    on_shift_ids = set()

    for shift in SHIFTS:
        if weekday not in shift["days"]:
            continue

        s = shift["start"]
        e = shift["end"]

        # Handle overnight shifts (e.g. 22:00 -> 06:00)
        if s <= e:
            in_shift = s <= now_time < e
        else:
            in_shift = now_time >= s or now_time < e

        if in_shift:
            for uid in shift["admins"]:
                on_shift_ids.add(uid)

    result = []
    for uid in on_shift_ids:
        info = ADMINS.get(uid, {})
        result.append({
            "id":       uid,
            "name":     info.get("name", f"Admin {uid}"),
            "username": info.get("username", ""),
        })

    return result


def get_all_admins() -> list[dict]:
    """Returns all admins regardless of shift."""
    return [
        {"id": uid, "name": info["name"], "username": info.get("username", "")}
        for uid, info in ADMINS.items()
    ]


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
