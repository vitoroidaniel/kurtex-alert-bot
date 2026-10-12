"""Persistent per-user frontend preferences (JSON-backed on DATA_DIR volume)."""
import json, os
from pathlib import Path
from threading import RLock
from contextlib import contextmanager
import fcntl

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
# Existing installations keep using the same file on their Railway volume.
_LEGACY_PREFERENCES = DATA_DIR / "dashboard_preferences.json"
PREFERENCES_FILE = _LEGACY_PREFERENCES if _LEGACY_PREFERENCES.exists() else DATA_DIR / "frontend_preferences.json"
_lock = RLock()
_LOCK_FILE = DATA_DIR / 'frontend_preferences.lock'

@contextmanager
def _storage_lock():
    # Thread and cross-worker process lock: multiple Railway workers must not
    # clobber each other's account preference writes.
    with _lock:
        with _LOCK_FILE.open('a+') as lockfile:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)


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
    with _storage_lock():
        row = _load().get(str(user_id), {})
        value = row.get("overview", {}) if isinstance(row, dict) else {}
        return value if isinstance(value, dict) else {}


def save_overview(user_id, value):
    with _storage_lock():
        data = _load()
        row = data.setdefault(str(user_id), {})
        row["overview"] = value
        _save(data)
        return value


def get_home_grid(user_id):
    with _storage_lock():
        row = _load().get(str(user_id), {})
        return row.get("home_grid", {}) if isinstance(row, dict) else {}

def save_home_grid(user_id, value):
    with _storage_lock():
        data = _load()
        row = data.setdefault(str(user_id), {})
        row["home_grid"] = value
        _save(data)
        return value


def get_home_notes(user_id):
    with _storage_lock():
        row = _load().get(str(user_id), {})
        result = row.get("home_notes", []) if isinstance(row, dict) else []
        return result if isinstance(result, list) else []

def save_home_notes(user_id, value):
    with _storage_lock():
        data = _load()
        row = data.setdefault(str(user_id), {})
        row["home_notes"] = value
        _save(data)
        return value


def get_interface(user_id):
    """Return the account's interface/notification settings, or an empty record."""
    with _storage_lock():
        row = _load().get(str(user_id), {})
        value = row.get("interface", {}) if isinstance(row, dict) else {}
        return dict(value) if isinstance(value, dict) else {}


def save_interface(user_id, value):
    """Update only interface settings; never erase saved layout, notes or widgets."""
    with _storage_lock():
        data = _load()
        row = data.setdefault(str(user_id), {})
        current = row.get("interface", {}) if isinstance(row.get("interface"), dict) else {}
        merged = {**current, **value}
        row["interface"] = merged
        _save(data)
        return dict(merged)
