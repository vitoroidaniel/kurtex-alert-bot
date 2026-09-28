"""
storage/user_store.py — Dynamic user/role management, JSON-backed.

Roles:
  developer  — full bot access, NO alert notifications
  super_admin — full bot access + alerts + report commands
  agent      — standard agent commands + alerts

Data stored at DATA_DIR/users.json (Railway Volume).
"""

import json
import logging
import os
from pathlib import Path

logger   = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"

VALID_ROLES = {"developer", "super_admin", "agent"}


def _load() -> dict:
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(users: dict) -> None:
    tmp = USERS_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(users, indent=2), encoding="utf-8")
        tmp.replace(USERS_FILE)
    except Exception as e:
        logger.error(f"Failed to save users: {e}")


def get_user(user_id: int) -> dict | None:
    return _load().get(str(user_id))


def get_all_users() -> dict:
    return _load()


def add_user(user_id: int, name: str, username: str, role: str) -> bool:
    if role not in VALID_ROLES:
        return False
    users = _load()
    users[str(user_id)] = {
        "name":     name,
        "username": username or "",
        "role":     role,
    }
    _save(users)
    logger.info(f"User added/updated: {user_id} ({name}) as {role}")
    return True


def remove_user(user_id: int) -> bool:
    users = _load()
    if str(user_id) not in users:
        return False
    del users[str(user_id)]
    _save(users)
    logger.info(f"User removed: {user_id}")
    return True


def edit_role(user_id: int, role: str) -> bool:
    if role not in VALID_ROLES:
        return False
    users = _load()
    if str(user_id) not in users:
        return False
    users[str(user_id)]["role"] = role
    _save(users)
    logger.info(f"Role changed: {user_id} → {role}")
    return True


def has_role(user_id: int, *roles: str) -> bool:
    u = get_user(user_id)
    return u is not None and u["role"] in roles


def is_authorized(user_id: int) -> bool:
    """Any registered user can use the bot."""
    return get_user(user_id) is not None


def get_alert_recipients() -> list[int]:
    """IDs that receive alert notifications — super_admin and agent only, NOT developer."""
    return [
        int(uid) for uid, u in _load().items()
        if u["role"] in ("super_admin", "agent")
    ]


def get_all_user_dicts() -> list[dict]:
    """All users as list of dicts with id included, for shift_manager compatibility."""
    return [
        {
            "id":       int(uid),
            "name":     u["name"],
            "username": u.get("username", ""),
            "role":     u["role"],
        }
        for uid, u in _load().items()
    ]
