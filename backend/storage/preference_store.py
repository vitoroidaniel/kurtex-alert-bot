"""Persistent per-user dashboard preferences (JSON-backed on DATA_DIR volume)."""
import json, os
from pathlib import Path
from threading import RLock

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PREFERENCES_FILE = DATA_DIR / "dashboard_preferences.json"
_lock = RLock()


def _load():
    if not PREFERENCES_FILE.exists():
        return {}
    try:
        data = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    tmp = PREFERENCES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PREFERENCES_FILE)


def get_overview(user_id):
    with _lock:
        row = _load().get(str(user_id), {})
        value = row.get("overview", {}) if isinstance(row, dict) else {}
        return value if isinstance(value, dict) else {}


def save_overview(user_id, value):
    with _lock:
        data = _load()
        row = data.setdefault(str(user_id), {})
        row["overview"] = value
        _save(data)
        return value


def get_home_grid(user_id):
    with _lock:
        row = _load().get(str(user_id), {})
        return row.get("home_grid", {}) if isinstance(row, dict) else {}

def save_home_grid(user_id, value):
    with _lock:
        data = _load()
        row = data.setdefault(str(user_id), {})
        row["home_grid"] = value
        _save(data)
        return value
