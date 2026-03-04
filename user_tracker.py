"""
user_tracker.py - Tracks which users have started the bot.

Stores user IDs in a JSON file for persistence across bot restarts.
"""

import json
import os
from pathlib import Path

# File to store started user IDs
DATA_FILE = Path(__file__).parent / "started_users.json"


def _load_users() -> set[int]:
    """Load set of user IDs from JSON file."""
    if not DATA_FILE.exists():
        return set()
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("started_users", []))
    except (json.JSONDecodeError, IOError):
        return set()


def _save_users(users: set[int]) -> None:
    """Save set of user IDs to JSON file."""
    with open(DATA_FILE, "w") as f:
        json.dump({"started_users": list(users)}, f)


def has_user_started(user_id: int) -> bool:
    """Check if a user has already started the bot."""
    users = _load_users()
    return user_id in users


def mark_user_started(user_id: int) -> None:
    """Mark a user as having started the bot."""
    users = _load_users()
    users.add(user_id)
    _save_users(users)

