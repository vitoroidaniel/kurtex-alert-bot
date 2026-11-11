"""SQLite-backed per-user frontend preferences on DATA_DIR volume."""

from __future__ import annotations

import json
import os
from pathlib import Path

from backend.core.app_time import utc_now
from backend.storage import db

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PREFERENCES_FILE = DATA_DIR / "frontend_preferences.json"


def _storage_dir() -> Path:
    env_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    if env_dir != DATA_DIR:
        env_dir.mkdir(parents=True, exist_ok=True)
        return env_dir
    return DATA_DIR


def _read_user_prefs(user_id) -> dict:
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute(
            "SELECT data_json FROM preferences WHERE user_id=?",
            (str(user_id),),
        ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row["data_json"])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _update_user_section(user_id, section: str, value) -> None:
    now = utc_now().isoformat()
    uid = str(user_id)
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute(
            "SELECT data_json FROM preferences WHERE user_id=?",
            (uid,),
        ).fetchone()
        data = {}
        if row:
            try:
                parsed = json.loads(row["data_json"])
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = {}
        if section == "interface":
            existing = data.get("interface")
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(value)
            data["interface"] = merged
        else:
            data[section] = value
        conn.execute(
            """INSERT INTO preferences(user_id, data_json, updated_at) VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at""",
            (uid, json.dumps(data, ensure_ascii=False, separators=(",", ":")), now),
        )


def get_overview(user_id):
    value = _read_user_prefs(user_id).get("overview", {})
    return value if isinstance(value, dict) else {}


def save_overview(user_id, value):
    _update_user_section(user_id, "overview", value)
    return value


def get_home_grid(user_id):
    value = _read_user_prefs(user_id).get("home_grid", {})
    return value if isinstance(value, dict) else {}


def save_home_grid(user_id, value):
    _update_user_section(user_id, "home_grid", value)
    return value


def get_home_notes(user_id):
    result = _read_user_prefs(user_id).get("home_notes", [])
    return result if isinstance(result, list) else []


def save_home_notes(user_id, value):
    _update_user_section(user_id, "home_notes", value)
    return value


def get_interface(user_id):
    """Return the account's interface/notification settings, or an empty record."""
    value = _read_user_prefs(user_id).get("interface", {})
    return dict(value) if isinstance(value, dict) else {}


def save_interface(user_id, value):
    """Update only interface settings; never erase saved layout, notes or widgets."""
    _update_user_section(user_id, "interface", value)
    return get_interface(user_id)
