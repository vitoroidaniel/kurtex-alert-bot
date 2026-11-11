"""shift_manager.py -- Resolves active alert recipients from DB-backed shifts."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.storage import shift_store, user_store


def _get_all_alert_users() -> list[dict]:
    """Pull all alert-eligible users (super_admin + agent), excluding developer."""
    return [
        u for u in user_store.get_all_user_dicts()
        if u["role"] in ("super_admin", "agent")
    ]


def get_on_shift_admins(now_utc: Optional[datetime] = None) -> list[dict]:
    """Return alert-eligible users assigned to currently active shift(s)."""
    state = shift_store.get_on_shift_state(now_utc)
    return state["on_duty_users"]


def get_all_admins() -> list[dict]:
    """Return all alert-eligible users regardless of shift (fallback)."""
    return _get_all_alert_users()


def get_current_shift_name(now_utc: Optional[datetime] = None) -> str:
    state = shift_store.get_on_shift_state(now_utc)
    active = state["active_shifts"]
    if active:
        return ", ".join(s["name"] for s in active)
    return "Off Hours" if state["has_configured_shifts"] else "No Shifts Configured"
