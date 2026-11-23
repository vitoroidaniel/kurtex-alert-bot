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


_WORKSPACE_BOARD_BOOL_DEFAULTS = {
    "background_alerts": True,
    "alert_sound": True,
    "ai_autocomplete": True,
    "ai_tab_badge": True,
    "hide_off_shift": True,
    "compact": False,
    "focus_mode": False,
}
_WORKSPACE_BOARD_BOOL_FIELDS = tuple(_WORKSPACE_BOARD_BOOL_DEFAULTS.keys())

_WORKSPACE_TOOLBAR_KEYS = (
    "priorities",
    "presets",
    "agent_filter",
    "my_cases",
    "watchlist",
    "has_shop",
    "unassigned",
    "off_shift",
    "zoom",
    "export_csv",
    "shift_summary",
    "compact",
    "refresh",
)

WORKSPACE_ZOOM_MIN = 70
WORKSPACE_ZOOM_MAX = 130
WORKSPACE_ZOOM_DEFAULT = 100


def _sanitize_board_zoom(raw) -> int:
    try:
        val = int(round(float(raw)))
    except (TypeError, ValueError):
        return WORKSPACE_ZOOM_DEFAULT
    if val < WORKSPACE_ZOOM_MIN:
        return WORKSPACE_ZOOM_MIN
    if val > WORKSPACE_ZOOM_MAX:
        return WORKSPACE_ZOOM_MAX
    return val


def _sanitize_key_list(raw, *, allow_colon: bool = True, max_items: int = 80, max_len: int = 64) -> list[str]:
    """Return bounded deduplicated list of safe identifier keys."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    allowed_extra = {"-", "_", ":"} if allow_colon else {"-", "_"}
    for item in raw:
        key = "".join(ch for ch in str(item or "").strip()[:max_len] if ch.isalnum() or ch in allowed_extra)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= max_items:
            break
    return out


def _sanitize_saved_view_conditions(raw_conds) -> list[dict]:
    if not isinstance(raw_conds, list):
        return []
    out = []
    for item in raw_conds[:20]:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()[:48]
        op = str(item.get("operator") or "contains").strip()[:48]
        if not field:
            continue
        val = str(item.get("value") or "")[:240]
        raw_vals = item.get("values")
        vals = [str(v)[:120] for v in raw_vals[:40] if v is not None] if isinstance(raw_vals, list) else []
        out.append({
            "field": field,
            "operator": op,
            "value": val,
            "values": vals,
        })
    return out


def _sanitize_saved_view_filters(raw_filters) -> dict:
    f = raw_filters if isinstance(raw_filters, dict) else {}
    prio = str(f.get("priority") or "all").strip().lower()
    if prio not in ("all", "high", "medium", "low", "gray"):
        prio = "all"
    view_mode = str(f.get("view") or "board").strip().lower()
    if view_mode not in ("board", "grid", "table"):
        view_mode = "board"
    match_mode = "any" if str(f.get("match_mode") or "all").strip().lower() == "any" else "all"
    return {
        "priority": prio,
        "agent": str(f.get("agent") or "all").strip()[:64] or "all",
        "my_cases": bool(f.get("my_cases", False)),
        "watchlist_only": bool(f.get("watchlist_only", False)),
        "has_shop": bool(f.get("has_shop", False)),
        "unassigned": bool(f.get("unassigned", False)),
        "hide_off_shift": bool(f.get("hide_off_shift", True)),
        "query": str(f.get("query") or "")[:160],
        "view": view_mode,
        "compact": bool(f.get("compact", False)),
        "match_mode": match_mode,
        "shared": bool(f.get("shared", False)),
        "include_parent_rows": bool(f.get("include_parent_rows", False)),
        "conditions": _sanitize_saved_view_conditions(f.get("conditions")),
    }


def _sanitize_workspace_board_prefs(raw) -> dict:
    src = raw if isinstance(raw, dict) else {}
    prefs = {
        key: bool(src.get(key, default_val))
        for key, default_val in _WORKSPACE_BOARD_BOOL_DEFAULTS.items()
    }
    raw_view = str(src.get("board_view") or "board").strip().lower()
    prefs["board_view"] = "grid" if raw_view in ("grid", "table") else "board"
    prefs["board_zoom"] = _sanitize_board_zoom(src.get("board_zoom", WORKSPACE_ZOOM_DEFAULT))
    collapsed_lanes = _sanitize_key_list(src.get("collapsed_lanes"), allow_colon=True, max_items=80, max_len=64)
    collapsed_set = set(collapsed_lanes)
    expanded_lanes = [
        k for k in _sanitize_key_list(src.get("expanded_lanes"), allow_colon=True, max_items=80, max_len=64)
        if k not in collapsed_set
    ]
    prefs["collapsed_lanes"] = collapsed_lanes
    prefs["expanded_lanes"] = expanded_lanes
    prefs["lane_order"] = _sanitize_key_list(src.get("lane_order"), allow_colon=True, max_items=120, max_len=64)
    prefs["watchlist_ids"] = _sanitize_key_list(src.get("watchlist_ids"), allow_colon=True, max_items=200, max_len=48)
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
                "pin_to_toolbar": bool(item.get("pin_to_toolbar", False)),
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
        if "board_view" in updates:
            merged["board_view"] = str(updates.get("board_view") or "board").strip().lower()
        if "board_zoom" in updates:
            merged["board_zoom"] = _sanitize_board_zoom(updates.get("board_zoom"))
        if "collapsed_lanes" in updates:
            merged["collapsed_lanes"] = updates.get("collapsed_lanes")
        if "expanded_lanes" in updates:
            merged["expanded_lanes"] = updates.get("expanded_lanes")
        if "lane_order" in updates:
            merged["lane_order"] = updates.get("lane_order")
        if "watchlist_ids" in updates:
            merged["watchlist_ids"] = updates.get("watchlist_ids")
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
