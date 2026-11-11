"""SQLite-backed shift configuration and per-user shift assignments."""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
import zoneinfo
from datetime import datetime, time, timedelta
from typing import Optional

from backend.core.app_time import utc_now
from backend.storage import db, user_store

logger = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_CACHE_LOCK = threading.RLock()
_SHIFTS_CACHE: dict[str, tuple[int, list[dict]]] = {}


def _validate_time(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not _TIME_RE.fullmatch(text):
        raise ValueError(f"{label} must be in HH:MM 24-hour format (e.g. 06:30).")
    return text


def _parse_time(value: str) -> time:
    hh, mm = map(int, value.split(":"))
    return time(hh, mm)


def _validate_days(days) -> list[int]:
    if not isinstance(days, list) or not days:
        raise ValueError("Select at least one day of the week.")
    cleaned = sorted({int(d) for d in days})
    if any(d < 0 or d > 6 for d in cleaned):
        raise ValueError("Days must be integers between 0 (Mon) and 6 (Sun).")
    return cleaned


def _validate_timezone(tz_name: str) -> str:
    text = str(tz_name or "America/Chicago").strip() or "America/Chicago"
    try:
        zoneinfo.ZoneInfo(text)
    except Exception as exc:
        raise ValueError(f"Unknown timezone: {text}") from exc
    return text


def _invalidate() -> None:
    with _CACHE_LOCK:
        _SHIFTS_CACHE.clear()


def list_shifts() -> list[dict]:
    sdir = db.get_data_dir()
    cache_key = str(sdir.resolve())
    with db.read_conn(sdir) as conn:
        ver_row = conn.execute("SELECT value FROM meta WHERE key='shifts_version'").fetchone()
        ver = int(ver_row["value"]) if ver_row else 0
        with _CACHE_LOCK:
            cached = _SHIFTS_CACHE.get(cache_key)
            if cached is not None and cached[0] == ver:
                return [dict(s, days=list(s["days"]), user_ids=list(s["user_ids"])) for s in cached[1]]
        shift_rows = conn.execute(
            "SELECT id, name, start_time, end_time, days_json, timezone, enabled, created_at, updated_at "
            "FROM shifts ORDER BY start_time ASC, name COLLATE NOCASE ASC"
        ).fetchall()
        assign_rows = conn.execute("SELECT user_id, shift_id FROM user_shifts").fetchall()
        by_shift: dict[str, list[int]] = {}
        for ar in assign_rows:
            by_shift.setdefault(ar["shift_id"], []).append(int(ar["user_id"]))
        shifts: list[dict] = []
        for r in shift_rows:
            try:
                days = json.loads(r["days_json"] or "[]")
            except Exception:
                days = []
            shifts.append({
                "id": r["id"],
                "name": r["name"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "days": [int(d) for d in days],
                "timezone": r["timezone"] or "America/Chicago",
                "enabled": bool(r["enabled"]),
                "user_ids": sorted(by_shift.get(r["id"], [])),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        with _CACHE_LOCK:
            _SHIFTS_CACHE[cache_key] = (ver, shifts)
        return [dict(s, days=list(s["days"]), user_ids=list(s["user_ids"])) for s in shifts]


def get_shift(shift_id: str) -> Optional[dict]:
    for s in list_shifts():
        if s["id"] == shift_id:
            return s
    return None


def _filter_valid_user_ids(user_ids) -> list[int]:
    if not user_ids:
        return []
    known = set(user_store.get_all_users().keys())
    cleaned = []
    for uid in user_ids:
        uid_int = int(uid)
        if str(uid_int) in known and uid_int not in cleaned:
            cleaned.append(uid_int)
    return cleaned


def create_shift(
    *,
    name: str,
    start_time: str,
    end_time: str,
    days: list[int],
    timezone: str = "America/Chicago",
    enabled: bool = True,
    user_ids: Optional[list[int]] = None,
) -> dict:
    clean_name = str(name or "").strip()[:60]
    if not clean_name:
        raise ValueError("Shift name is required.")
    s_time = _validate_time(start_time, "Start time")
    e_time = _validate_time(end_time, "End time")
    if s_time == e_time:
        raise ValueError("Start time and end time cannot be identical.")
    clean_days = _validate_days(days)
    clean_tz = _validate_timezone(timezone)
    assigned = _filter_valid_user_ids(user_ids) if user_ids is not None else []
    shift_id = str(uuid.uuid4())[:12]
    now = utc_now().isoformat()

    with db.write_tx() as conn:
        conn.execute(
            """INSERT INTO shifts(id, name, start_time, end_time, days_json, timezone, enabled, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (shift_id, clean_name, s_time, e_time, json.dumps(clean_days), clean_tz, 1 if enabled else 0, now, now),
        )
        for uid in assigned:
            conn.execute(
                "INSERT OR IGNORE INTO user_shifts(user_id, shift_id) VALUES(?,?)",
                (uid, shift_id),
            )
        db.bump_version(conn, "shifts_version")
    _invalidate()
    return get_shift(shift_id)  # type: ignore[return-value]


def update_shift(
    shift_id: str,
    *,
    name: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    days: Optional[list[int]] = None,
    timezone: Optional[str] = None,
    enabled: Optional[bool] = None,
    user_ids: Optional[list[int]] = None,
) -> dict:
    existing = get_shift(shift_id)
    if not existing:
        raise LookupError("Shift not found.")
    clean_name = existing["name"] if name is None else str(name or "").strip()[:60]
    if not clean_name:
        raise ValueError("Shift name is required.")
    s_time = existing["start_time"] if start_time is None else _validate_time(start_time, "Start time")
    e_time = existing["end_time"] if end_time is None else _validate_time(end_time, "End time")
    if s_time == e_time:
        raise ValueError("Start time and end time cannot be identical.")
    clean_days = existing["days"] if days is None else _validate_days(days)
    clean_tz = existing["timezone"] if timezone is None else _validate_timezone(timezone)
    clean_enabled = existing["enabled"] if enabled is None else bool(enabled)
    now = utc_now().isoformat()

    with db.write_tx() as conn:
        conn.execute(
            """UPDATE shifts SET name=?, start_time=?, end_time=?, days_json=?, timezone=?, enabled=?, updated_at=?
               WHERE id=?""",
            (clean_name, s_time, e_time, json.dumps(clean_days), clean_tz, 1 if clean_enabled else 0, now, shift_id),
        )
        if user_ids is not None:
            assigned = _filter_valid_user_ids(user_ids)
            conn.execute("DELETE FROM user_shifts WHERE shift_id=?", (shift_id,))
            for uid in assigned:
                conn.execute(
                    "INSERT OR IGNORE INTO user_shifts(user_id, shift_id) VALUES(?,?)",
                    (uid, shift_id),
                )
        db.bump_version(conn, "shifts_version")
    _invalidate()
    return get_shift(shift_id)  # type: ignore[return-value]


def delete_shift(shift_id: str) -> bool:
    with db.write_tx() as conn:
        cur = conn.execute("DELETE FROM shifts WHERE id=?", (shift_id,))
        if cur.rowcount == 0:
            return False
        db.bump_version(conn, "shifts_version")
    _invalidate()
    return True


def set_user_shifts(user_id: int, shift_ids: list[str]) -> list[str]:
    if not user_store.get_user(int(user_id)):
        raise LookupError("User not found.")
    valid_shift_ids = {s["id"] for s in list_shifts()}
    cleaned: list[str] = []
    for sid in shift_ids or []:
        sid_str = str(sid).strip()
        if sid_str not in valid_shift_ids:
            raise ValueError(f"Unknown shift: {sid_str}")
        if sid_str not in cleaned:
            cleaned.append(sid_str)
    with db.write_tx() as conn:
        conn.execute("DELETE FROM user_shifts WHERE user_id=?", (int(user_id),))
        for sid in cleaned:
            conn.execute(
                "INSERT OR IGNORE INTO user_shifts(user_id, shift_id) VALUES(?,?)",
                (int(user_id), sid),
            )
        db.bump_version(conn, "shifts_version")
    _invalidate()
    return cleaned


def is_shift_active_at(shift: dict, now_utc: Optional[datetime] = None) -> bool:
    """Evaluate a shift schedule in its own timezone, handling overnight wraps accurately."""
    if not shift.get("enabled"):
        return False
    days = set(shift.get("days") or [])
    if not days:
        return False
    try:
        tz = zoneinfo.ZoneInfo(shift.get("timezone") or "America/Chicago")
    except Exception:
        tz = zoneinfo.ZoneInfo("America/Chicago")
    ref = (now_utc or utc_now()).astimezone(tz)
    now_t = ref.time().replace(second=0, microsecond=0)
    weekday = ref.weekday()
    start_t = _parse_time(shift["start_time"])
    end_t = _parse_time(shift["end_time"])
    if start_t < end_t:
        return weekday in days and (start_t <= now_t < end_t)
    # Overnight window (e.g. 23:00 -> 07:00): after midnight belongs to the day it started.
    if now_t >= start_t:
        return weekday in days
    if now_t < end_t:
        prev_day = (ref - timedelta(days=1)).weekday()
        return prev_day in days
    return False


def get_on_shift_state(now_utc: Optional[datetime] = None) -> dict:
    """Return active shifts, assigned on-duty alert users, and fallback metadata."""
    ref = now_utc or utc_now()
    shifts = list_shifts()
    all_users = user_store.get_all_user_dicts()
    alert_users_by_id = {
        u["id"]: u for u in all_users if u.get("role") in ("super_admin", "agent")
    }

    active_shifts = [s for s in shifts if is_shift_active_at(s, ref)]
    on_duty_ids: set[int] = set()
    for s in active_shifts:
        for uid in s.get("user_ids") or []:
            if uid in alert_users_by_id:
                on_duty_ids.add(uid)

    on_duty_users = sorted(
        (alert_users_by_id[uid] for uid in on_duty_ids),
        key=lambda u: ((u.get("name") or "").casefold(), u["id"]),
    )
    has_configured_shifts = any(s.get("enabled") for s in shifts)
    return {
        "shifts": shifts,
        "active_shifts": active_shifts,
        "on_duty_users": on_duty_users,
        "on_duty_user_ids": [u["id"] for u in on_duty_users],
        "has_configured_shifts": has_configured_shifts,
        "using_fallback": len(on_duty_users) == 0,
    }
