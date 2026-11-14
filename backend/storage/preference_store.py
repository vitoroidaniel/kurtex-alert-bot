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


def _read_user_prefs(user_id, data_dir: Path | None = None) -> dict:
    with db.read_conn(data_dir or _storage_dir()) as conn:
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


def _mutate_user_prefs(user_id, mutator, data_dir: Path | None = None):
    """Run an atomic read-modify-write transform on a user's preferences JSON."""
    now = utc_now().isoformat()
    uid = str(user_id)
    with db.write_tx(data_dir or _storage_dir()) as conn:
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
        result = mutator(data)
        conn.execute(
            """INSERT INTO preferences(user_id, data_json, updated_at) VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at""",
            (uid, json.dumps(data, ensure_ascii=False, separators=(",", ":")), now),
        )
        return result


def _update_user_section(user_id, section: str, value, data_dir: Path | None = None) -> None:
    def _apply(data):
        if section == "interface":
            existing = data.get("interface")
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(value)
            data["interface"] = merged
        else:
            data[section] = value
    _mutate_user_prefs(user_id, _apply, data_dir=data_dir)


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


def get_quick_replies(user_id, data_dir: Path | None = None):
    """Return custom user quick replies list or None if using defaults."""
    value = _read_user_prefs(user_id, data_dir=data_dir).get("quick_replies")
    return value if isinstance(value, list) else None


def save_quick_replies(user_id, value, data_dir: Path | None = None):
    """Persist custom user quick replies list (or None to reset to defaults)."""
    _update_user_section(user_id, "quick_replies", value, data_dir=data_dir)
    return get_quick_replies(user_id, data_dir=data_dir)


def get_case_reads(user_id, data_dir: Path | None = None) -> dict[str, str]:
    """Return map of case_id -> ISO timestamp when user last read case comments."""
    value = _read_user_prefs(user_id, data_dir=data_dir).get("case_reads", {})
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if k and isinstance(v, str)}


def save_case_reads(user_id, updates: dict[str, str], data_dir: Path | None = None) -> dict[str, str]:
    """Atomically merge monotonic read timestamps for a user and return updated map."""
    def _apply(data):
        existing = data.get("case_reads")
        merged = {
            str(k): str(v)
            for k, v in (existing.items() if isinstance(existing, dict) else ())
            if k and isinstance(v, str)
        }
        for cid, ts in (updates or {}).items():
            key = str(cid or "").strip()
            val = str(ts or "").strip()
            if not key or not val:
                continue
            prev = merged.get(key, "")
            if not prev or val > prev:
                merged[key] = val
        if len(merged) > 600:
            merged = dict(sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:600])
        data["case_reads"] = merged
        return merged

    return _mutate_user_prefs(user_id, _apply, data_dir=data_dir)


_WORKSPACE_BOARD_BOOL_FIELDS = (
    "background_alerts",
    "alert_sound",
    "ai_autocomplete",
    "ai_tab_badge",
)

_WORKSPACE_TOOLBAR_KEYS = (
    "priorities",
    "agent_filter",
    "my_cases",
    "has_shop",
    "unassigned",
    "off_shift",
    "export_csv",
    "compact",
    "refresh",
)


def _sanitize_saved_view_filters(raw_filters) -> dict:
    f = raw_filters if isinstance(raw_filters, dict) else {}
    prio = str(f.get("priority") or "all").strip().lower()
    if prio not in ("all", "high", "medium", "low"):
        prio = "all"
    view_mode = str(f.get("view") or "board").strip().lower()
    if view_mode not in ("board", "grid"):
        view_mode = "board"
    return {
        "priority": prio,
        "agent": str(f.get("agent") or "all").strip()[:64] or "all",
        "my_cases": bool(f.get("my_cases", False)),
        "has_shop": bool(f.get("has_shop", False)),
        "unassigned": bool(f.get("unassigned", False)),
        "hide_off_shift": bool(f.get("hide_off_shift", True)),
        "query": str(f.get("query") or "")[:160],
        "view": view_mode,
        "compact": bool(f.get("compact", False)),
    }


def _sanitize_workspace_board_prefs(raw) -> dict:
    src = raw if isinstance(raw, dict) else {}
    prefs = {
        key: bool(src.get(key, True))
        for key in _WORKSPACE_BOARD_BOOL_FIELDS
    }
    raw_tb = src.get("toolbar_visibility")
    tb = raw_tb if isinstance(raw_tb, dict) else {}
    prefs["toolbar_visibility"] = {
        key: bool(tb.get(key, True))
        for key in _WORKSPACE_TOOLBAR_KEYS
    }
    views = []
    seen_ids = set()
    raw_views = src.get("saved_views")
    if isinstance(raw_views, list):
        for item in raw_views:
            if not isinstance(item, dict):
                continue
            vid = "".join(ch for ch in str(item.get("id") or "").strip()[:48] if ch.isalnum() or ch in ("-", "_"))
            name = " ".join(str(item.get("name") or "").split()).strip()[:48]
            if not vid or not name or vid in seen_ids:
                continue
            seen_ids.add(vid)
            entry = {
                "id": vid,
                "name": name,
                "filters": _sanitize_saved_view_filters(item.get("filters")),
            }
            created_at = str(item.get("created_at") or "").strip()[:40]
            if created_at:
                entry["created_at"] = created_at
            views.append(entry)
            if len(views) >= 20:
                break
    prefs["saved_views"] = views
    prefs["active_view_id"] = str(src.get("active_view_id") or "").strip()[:64]
    return prefs


def get_workspace_board_prefs(user_id, data_dir: Path | None = None) -> dict:
    """Return validated Live Board workspace settings, toolbar layout, and saved views."""
    raw = _read_user_prefs(user_id, data_dir=data_dir).get("workspace_board")
    return _sanitize_workspace_board_prefs(raw)


def save_workspace_board_prefs(user_id, payload: dict, data_dir: Path | None = None) -> dict:
    """Atomically merge/update validated Live Board workspace settings and return stored copy."""
    updates = payload if isinstance(payload, dict) else {}

    def _apply(data):
        if updates.get("reset") is True:
            clean = _sanitize_workspace_board_prefs({})
            data["workspace_board"] = clean
            return clean
        merged = _sanitize_workspace_board_prefs(data.get("workspace_board"))
        for key in _WORKSPACE_BOARD_BOOL_FIELDS:
            if key in updates:
                merged[key] = bool(updates[key])
        if isinstance(updates.get("toolbar_visibility"), dict):
            tb_patch = updates["toolbar_visibility"]
            for tk in _WORKSPACE_TOOLBAR_KEYS:
                if tk in tb_patch:
                    merged["toolbar_visibility"][tk] = bool(tb_patch[tk])
        if "saved_views" in updates:
            merged["saved_views"] = _sanitize_workspace_board_prefs({"saved_views": updates["saved_views"]})["saved_views"]
        if "active_view_id" in updates:
            merged["active_view_id"] = str(updates.get("active_view_id") or "").strip()[:64]
        clean = _sanitize_workspace_board_prefs(merged)
        data["workspace_board"] = clean
        return clean

    return _mutate_user_prefs(user_id, _apply, data_dir=data_dir)
