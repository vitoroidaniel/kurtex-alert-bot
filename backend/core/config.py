"""Runtime configuration.

Secrets are read only from environment variables. Non-secret deployment settings can
live in DATA_DIR/settings.json on the Railway Volume, with environment variables
optionally overriding them.
"""
import json
import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"


def _settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _required_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _int_setting(name: str, key: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if raw:
        return int(raw)
    return int(_settings().get(key, default) or default)


class Config:
    TELEGRAM_TOKEN = _required_secret("BOT_TOKEN")
    DRIVER_GROUP_ID = _int_setting("DRIVER_GROUP_ID", "driver_group_id")
    REPORTS_GROUP_ID = _int_setting("REPORTS_GROUP_ID", "reports_group_id")
    DATA_DIR = str(DATA_DIR)


config = Config()
