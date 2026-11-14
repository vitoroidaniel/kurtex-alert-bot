"""Shared-volume ephemeral Live Board presence, with automatic 35-second expiry."""
from __future__ import annotations
import re
import time
from backend.storage import db

_TABLE = '''CREATE TABLE IF NOT EXISTS workspace_presence (
 session_id TEXT PRIMARY KEY,
 user_id INTEGER NOT NULL,
 user_name TEXT NOT NULL,
 action TEXT NOT NULL,
 case_id TEXT NOT NULL DEFAULT '',
 field TEXT NOT NULL DEFAULT '',
 updated_at REAL NOT NULL,
 expires_at REAL NOT NULL
)'''
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
ACTIONS = frozenset(("viewing", "editing", "dragging", "idle"))

def heartbeat(session_id, user_id, user_name, action="idle", case_id="", field="", data_dir=None):
    if not isinstance(session_id, str) or not _UUID.fullmatch(session_id):
        raise ValueError("Invalid board session.")
    if action not in ACTIONS:
        raise ValueError("Invalid collaboration activity.")
    if len(str(case_id)) > 100 or len(str(field)) > 60:
        raise ValueError("Invalid collaboration context.")
    now = time.time()
    with db.write_tx(data_dir) as conn:
        conn.execute(_TABLE)
        conn.execute("DELETE FROM workspace_presence WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT INTO workspace_presence(session_id,user_id,user_name,action,case_id,field,updated_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
            "user_id=excluded.user_id,user_name=excluded.user_name,action=excluded.action,"
            "case_id=excluded.case_id,field=excluded.field,updated_at=excluded.updated_at,expires_at=excluded.expires_at",
            (session_id, user_id, str(user_name)[:70], action, str(case_id), str(field), now, now + 35)
        )

def snapshot(current_user, data_dir=None):
    now = time.time()
    with db.read_conn(data_dir) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_presence'").fetchone()
        if not exists:
            return {"online": 0, "peers": []}
        rows = conn.execute(
            "SELECT user_id,user_name,action,case_id,field,updated_at FROM workspace_presence "
            "WHERE expires_at >= ? ORDER BY updated_at DESC LIMIT 100", (now,)
        ).fetchall()
    unique = {}
    for row in rows:
        uid = int(row["user_id"])
        if uid not in unique:
            unique[uid] = {"user_id": uid, "name": row["user_name"], "action": row["action"],
                           "case_id": row["case_id"], "field": row["field"]}
    return {"online": len(unique), "peers": [p for k,p in unique.items() if k != int(current_user)]}
