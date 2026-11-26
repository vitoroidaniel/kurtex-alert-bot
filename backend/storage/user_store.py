"""SQLite-backed dynamic user/role management with fast versioned caching.

Roles:
  developer   -- full bot/web access, NO automatic alert notifications
  super_admin -- full bot access + alerts + report commands
  agent       -- standard agent commands + alerts
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from backend.core.app_time import utc_now
from backend.storage import db

logger = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"

VALID_ROLES = {"developer", "super_admin", "agent"}

_CACHE_LOCK = threading.RLock()
_CACHE_TTL_SECS = 1.0
_USERS_CACHE: dict[str, tuple[float, int, dict[str, dict]]] = {}


def _storage_dir() -> Path:
    default_users = Path(os.getenv("DATA_DIR", "/app/data")) / "users.json"
    if USERS_FILE != default_users and USERS_FILE.parent != DATA_DIR:
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        return USERS_FILE.parent
    env_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    if env_dir != DATA_DIR:
        env_dir.mkdir(parents=True, exist_ok=True)
        return env_dir
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _load() -> dict[str, dict]:
    sdir = _storage_dir()
    cache_key = str(sdir.resolve())
    now_mono = time.monotonic()
    with _CACHE_LOCK:
        cached = _USERS_CACHE.get(cache_key)
        if cached is not None and (now_mono - cached[0]) < _CACHE_TTL_SECS:
            return {k: dict(v) for k, v in cached[2].items()}
    with db.read_conn(sdir) as conn:
        ver_row = conn.execute("SELECT value FROM meta WHERE key='users_version'").fetchone()
        ver = int(ver_row["value"]) if ver_row else 0
        with _CACHE_LOCK:
            cached = _USERS_CACHE.get(cache_key)
            if cached is not None and cached[1] == ver:
                _USERS_CACHE[cache_key] = (now_mono, ver, cached[2])
                return {k: dict(v) for k, v in cached[2].items()}
        rows = conn.execute("SELECT id, name, username, role, extra_json FROM users").fetchall()
        users: dict[str, dict] = {}
        for r in rows:
            extra = {}
            try:
                parsed = json.loads(r["extra_json"] or "{}")
                if isinstance(parsed, dict):
                    extra = parsed
            except Exception:
                pass
            users[str(r["id"])] = {
                **extra,
                "name": r["name"],
                "username": r["username"],
                "role": r["role"],
            }
        with _CACHE_LOCK:
            _USERS_CACHE[cache_key] = (time.monotonic(), ver, users)
        return {k: dict(v) for k, v in users.items()}


def _invalidate_cache(sdir: Path) -> None:
    with _CACHE_LOCK:
        _USERS_CACHE.pop(str(sdir.resolve()), None)


def get_user(user_id: int) -> dict | None:
    return _load().get(str(user_id))


def get_all_users() -> dict:
    return _load()


def add_user(user_id: int, name: str, username: str, role: str) -> bool:
    if role not in VALID_ROLES:
        return False
    sdir = _storage_dir()
    now = utc_now().isoformat()
    clean_uname = (username or "").strip().lstrip("@")
    with db.write_tx(sdir) as conn:
        conn.execute(
            """INSERT INTO users(id, name, username, role, extra_json, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name,
                   username=excluded.username,
                   role=excluded.role,
                   updated_at=excluded.updated_at""",
            (int(user_id), name, clean_uname, role, "{}", now),
        )
        db.bump_version(conn, "users_version")
    _invalidate_cache(sdir)
    logger.info("User added/updated: %s (%s) as %s", user_id, name, role)
    return True


def remove_user(user_id: int) -> bool:
    sdir = _storage_dir()
    with db.write_tx(sdir) as conn:
        cur = conn.execute("DELETE FROM users WHERE id=?", (int(user_id),))
        if cur.rowcount == 0:
            return False
        conn.execute("DELETE FROM user_shifts WHERE user_id=?", (int(user_id),))
        db.bump_version(conn, "users_version")
        db.bump_version(conn, "shifts_version")
    _invalidate_cache(sdir)
    logger.info("User removed: %s", user_id)
    return True


def edit_role(user_id: int, role: str) -> bool:
    if role not in VALID_ROLES:
        return False
    sdir = _storage_dir()
    now = utc_now().isoformat()
    with db.write_tx(sdir) as conn:
        cur = conn.execute(
            "UPDATE users SET role=?, updated_at=? WHERE id=?",
            (role, now, int(user_id)),
        )
        if cur.rowcount == 0:
            return False
        db.bump_version(conn, "users_version")
    _invalidate_cache(sdir)
    logger.info("Role changed: %s -> %s", user_id, role)
    return True


_ALLOWED_EXTRA_KEYS = ("title", "phone", "email", "notes")


def edit_user(
    user_id: int,
    name: str,
    username: str,
    role: str,
    *,
    new_user_id: int | None = None,
    extra: dict | None = None,
) -> bool:
    """Update an existing user's display details, profile metadata, role, and optional Telegram ID."""
    if role not in VALID_ROLES:
        return False
    sdir = _storage_dir()
    now = utc_now().isoformat()
    clean_uname = (username or "").strip().lstrip("@")
    uid = int(user_id)
    target_uid = int(new_user_id) if new_user_id is not None else uid
    with db.write_tx(sdir) as conn:
        row = conn.execute("SELECT id, extra_json FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            return False
        if target_uid != uid:
            clash = conn.execute("SELECT 1 FROM users WHERE id=?", (target_uid,)).fetchone()
            if clash:
                raise ValueError("Another user already uses that Telegram ID.")
        try:
            merged_extra = json.loads(row["extra_json"] or "{}")
            if not isinstance(merged_extra, dict):
                merged_extra = {}
        except Exception:
            merged_extra = {}
        if isinstance(extra, dict):
            for k in _ALLOWED_EXTRA_KEYS:
                if k in extra:
                    val = str(extra[k] or "").strip()[:300 if k == "notes" else 120]
                    if val:
                        merged_extra[k] = val
                    else:
                        merged_extra.pop(k, None)
        extra_str = json.dumps(merged_extra, ensure_ascii=False, separators=(",", ":"))
        conn.execute(
            "UPDATE users SET id=?, name=?, username=?, role=?, extra_json=?, updated_at=? WHERE id=?",
            (target_uid, name.strip(), clean_uname, role, extra_str, now, uid),
        )
        if target_uid != uid:
            conn.execute("UPDATE user_shifts SET user_id=? WHERE user_id=?", (target_uid, uid))
            conn.execute("UPDATE preferences SET user_id=? WHERE user_id=?", (str(target_uid), str(uid)))
            case_rows = conn.execute("SELECT id, data_json FROM cases WHERE agent_id=?", (uid,)).fetchall()
            for crow in case_rows:
                try:
                    cdict = json.loads(crow["data_json"])
                except Exception:
                    continue
                if isinstance(cdict, dict):
                    cdict["agent_id"] = target_uid
                    cdict["agent_name"] = name.strip()
                    cdict["agent_username"] = clean_uname
                    conn.execute(
                        "UPDATE cases SET agent_id=?, data_json=? WHERE id=?",
                        (target_uid, json.dumps(cdict, ensure_ascii=False, separators=(",", ":")), crow["id"]),
                    )
            db.bump_version(conn, "shifts_version")
            db.bump_version(conn, "cases_version")
        db.bump_version(conn, "users_version")
    _invalidate_cache(sdir)
    logger.info("User updated: %s -> %s (%s) as %s", uid, target_uid, name, role)
    return True


def sync_telegram_username(user_id: int, username: str) -> bool:
    """Refresh an authorized user's Telegram username only when it actually changed."""
    clean = (username or "").strip().lstrip("@")
    existing = get_user(user_id)
    if not existing or existing.get("username", "") == clean:
        return False
    sdir = _storage_dir()
    now = utc_now().isoformat()
    with db.write_tx(sdir) as conn:
        cur = conn.execute(
            "UPDATE users SET username=?, updated_at=? WHERE id=? AND username!=?",
            (clean, now, int(user_id), clean),
        )
        if cur.rowcount == 0:
            return False
        db.bump_version(conn, "users_version")
    _invalidate_cache(sdir)
    logger.info("Telegram username refreshed: %s -> @%s", user_id, clean or "none")
    return True


def has_role(user_id: int, *roles: str) -> bool:
    u = get_user(user_id)
    return u is not None and u["role"] in roles


def is_authorized(user_id: int) -> bool:
    """Any registered user can use the bot."""
    return get_user(user_id) is not None


def get_alert_recipients() -> list[int]:
    """IDs that receive alert notifications -- super_admin and agent only, NOT developer."""
    return [
        int(uid)
        for uid, u in _load().items()
        if u["role"] in ("super_admin", "agent") and not u.get("synthetic_staging")
    ]


def get_all_user_dicts() -> list[dict]:
    """All users as list of dicts with id and optional profile fields included."""
    out = []
    for uid, u in _load().items():
        item = {
            "id":       int(uid),
            "name":     u["name"],
            "username": u.get("username", ""),
            "role":     u["role"],
        }
        for k in _ALLOWED_EXTRA_KEYS:
            if u.get(k):
                item[k] = u[k]
        out.append(item)
    return out
