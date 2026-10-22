"""Versioned shared Kanban ordering; no changes to Telegram case records."""
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

_KEY = re.compile(r"^[a-z_]{1,32}:(agent|stage|done|load):[A-Za-z0-9_.:@ -]{0,128}$")
_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")


def _path(data_dir):
    return Path(data_dir) / "workspace_card_order.json"


def snapshot(data_dir):
    try:
        value = json.loads(_path(data_dir).read_text(encoding="utf-8"))
        if isinstance(value, dict) and type(value.get("version")) is int and isinstance(value.get("groups"), dict):
            return value
    except (OSError, ValueError):
        pass
    return {"version": 0, "groups": {}}


def update(data_dir, group, ids, expected_version):
    if not isinstance(group, str) or not _KEY.fullmatch(group):
        raise ValueError("Invalid board column")
    if (not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(i, str) or not _ID.fullmatch(i) for i in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("Invalid card order")
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("Invalid order version")
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = snapshot(data_dir)
            if state["version"] != expected_version:
                return False, state
            if group not in state["groups"] and len(state["groups"]) >= 250:
                raise ValueError("Too many board columns")
            state["groups"][group] = ids
            state["version"] += 1
            fd, temp = tempfile.mkstemp(prefix=".workspace_order.", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(state, stream, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            return True, state
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
