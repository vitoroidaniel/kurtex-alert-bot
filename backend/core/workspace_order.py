"""SQLite-backed shared Kanban ordering."""

from __future__ import annotations

import json
import re
from pathlib import Path

from backend.storage import db

_KEY = re.compile(r"^[a-z_]{1,32}:(agent|stage|done|load):[A-Za-z0-9_.:@ -]{0,128}$")
_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
_STATE_KEY = "workspace_card_order"


def snapshot(data_dir):
    try:
        with db.read_conn(Path(data_dir)) as conn:
            row = conn.execute(
                "SELECT version, data_json FROM kv_state WHERE key=?",
                (_STATE_KEY,),
            ).fetchone()
        if row:
            groups = json.loads(row["data_json"])
            if isinstance(groups, dict):
                return {"version": int(row["version"]), "groups": groups}
    except Exception:
        pass
    return {"version": 0, "groups": {}}


def update(data_dir, group, ids, expected_version):
    if not isinstance(group, str) or not _KEY.fullmatch(group):
        raise ValueError("Invalid board column")
    if (
        not isinstance(ids, list)
        or len(ids) > 500
        or any(not isinstance(i, str) or not _ID.fullmatch(i) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Invalid card order")
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("Invalid order version")
    with db.write_tx(Path(data_dir)) as conn:
        row = conn.execute(
            "SELECT version, data_json FROM kv_state WHERE key=?",
            (_STATE_KEY,),
        ).fetchone()
        if row:
            cur_ver = int(row["version"])
            groups = json.loads(row["data_json"]) if row["data_json"] else {}
        else:
            cur_ver = 0
            groups = {}
        state = {"version": cur_ver, "groups": groups}
        if cur_ver != expected_version:
            return False, state
        if group not in groups and len(groups) >= 250:
            raise ValueError("Too many board columns")
        groups[group] = ids
        next_ver = cur_ver + 1
        conn.execute(
            """INSERT INTO kv_state(key, version, data_json) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET version=excluded.version, data_json=excluded.data_json""",
            (_STATE_KEY, next_ver, json.dumps(groups, separators=(",", ":"))),
        )
        return True, {"version": next_ver, "groups": groups}
