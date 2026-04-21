"""
user_tracker.py — Volume-backed started_users registry.
Stores user IDs in /app/data/started_users.json (Railway Volume).
Atomic writes prevent corruption on crash.
"""

import json
import logging
import os
from pathlib import Path

logger    = logging.getLogger(__name__)
DATA_DIR  = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_DIR / "started_users.json"


def _load() -> set[int]:
    if not DATA_FILE.exists():
        return set()
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return set(data.get("started_users", []))
    except Exception:
        return set()


def _save(users: set[int]) -> None:
    tmp = DATA_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps({"started_users": list(users)}),
            encoding="utf-8",
        )
        tmp.replace(DATA_FILE)
    except Exception as e:
        logger.error(f"Failed to save started_users: {e}")


def has_user_started(user_id: int) -> bool:
    return user_id in _load()


def mark_user_started(user_id: int) -> None:
    users = _load()
    if user_id not in users:
        users.add(user_id)
        _save(users)


# async shims for handlers that use await
async def async_has_user_started(user_id: int) -> bool:
    return has_user_started(user_id)

async def async_mark_user_started(user_id: int) -> None:
    mark_user_started(user_id)
