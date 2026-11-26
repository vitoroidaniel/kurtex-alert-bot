"""Shared-volume ephemeral Live Board presence, with automatic 50-second expiry."""
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
 draft TEXT NOT NULL DEFAULT '',
 updated_at REAL NOT NULL,
 expires_at REAL NOT NULL
)'''
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
ACTIONS = frozenset(("viewing", "editing", "dragging", "idle", "left"))
_ACTIVE_ACTIONS = frozenset(("viewing", "editing", "dragging", "idle"))
_ACTION_RANK = {"editing": 4, "dragging": 3, "viewing": 2, "idle": 1}


_ENSURED_DIRS: set[str] = set()


def _ensure_table(conn, data_dir=None):
    key = str(db.get_data_dir(data_dir).resolve())
    if key in _ENSURED_DIRS:
        return
    conn.execute(_TABLE)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(workspace_presence)").fetchall()}
    if "draft" not in cols:
        conn.execute("ALTER TABLE workspace_presence ADD COLUMN draft TEXT NOT NULL DEFAULT ''")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_workspace_presence_expiry ON workspace_presence(expires_at)')
    _ENSURED_DIRS.add(key)


def heartbeat(session_id, user_id, user_name, action="idle", case_id="", field="", data_dir=None, draft=""):
    if not isinstance(session_id, str) or not _UUID.fullmatch(session_id):
        raise ValueError("Invalid board session.")
    if action not in ACTIONS:
        raise ValueError("Invalid collaboration activity.")
    if action == "left":
        remove_session(session_id, data_dir=data_dir, user_id=user_id)
        return
    if len(str(case_id)) > 100 or len(str(field)) > 60:
        raise ValueError("Invalid collaboration context.")
    clean_draft = str(draft or "").strip()[:240]
    now = time.time()
    with db.write_tx(data_dir) as conn:
        _ensure_table(conn, data_dir)
        conn.execute("DELETE FROM workspace_presence WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT INTO workspace_presence(session_id,user_id,user_name,action,case_id,field,draft,updated_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
            "user_id=excluded.user_id,user_name=excluded.user_name,action=excluded.action,"
            "case_id=excluded.case_id,field=excluded.field,draft=excluded.draft,"
            "updated_at=excluded.updated_at,expires_at=excluded.expires_at "
            "WHERE workspace_presence.user_id=excluded.user_id",
            (session_id, user_id, str(user_name)[:70], action, str(case_id), str(field), clean_draft, now, now + 50)
        )


def remove_session(session_id, data_dir=None, *, user_id=None):
    """Remove an ephemeral session immediately when a realtime socket closes."""
    if not isinstance(session_id, str) or not _UUID.fullmatch(session_id):
        return
    with db.write_tx(data_dir) as conn:
        _ensure_table(conn, data_dir)
        if user_id is None:
            conn.execute("DELETE FROM workspace_presence WHERE session_id=?", (session_id,))
        else:
            conn.execute("DELETE FROM workspace_presence WHERE session_id=? AND user_id=?", (session_id, user_id))


def snapshot(current_user, data_dir=None, *, include_activity: bool = True):
    now = time.time()
    with db.read_conn(data_dir) as conn:
        try:
            rows = conn.execute(
                "SELECT user_id,user_name,action,case_id,field,draft,updated_at FROM workspace_presence "
                "WHERE expires_at >= ? ORDER BY updated_at DESC LIMIT 200", (now,)
            ).fetchall()
        except Exception:
            return {"online": 0, "editing": 0, "peers": [], "members": []}
    unique = {}
    for row in rows:
        uid = int(row["user_id"])
        act = str(row["action"] or "idle")
        if act not in _ACTIVE_ACTIONS:
            continue
        rank = _ACTION_RANK.get(act, 1) if include_activity else 1
        upd = float(row["updated_at"] or 0.0)
        prev = unique.get(uid)
        if not prev or rank > prev["_rank"] or (rank == prev["_rank"] and upd > prev["_upd"]):
            unique[uid] = {
                "user_id": uid,
                "name": row["user_name"],
                "action": act if include_activity else "idle",
                "case_id": row["case_id"] if include_activity else "",
                "field": row["field"] if include_activity else "",
                "draft": (row["draft"] or "") if include_activity else "",
                "updated_at": upd,
                "_rank": rank,
                "_upd": upd,
            }
    ordered = sorted(
        unique.values(),
        key=lambda item: (-item["_rank"], str(item["name"] or "").casefold(), item["user_id"]),
    )
    members = [
        {k: v for k, v in item.items() if not k.startswith("_")}
        for item in ordered
    ]
    peers = [p for p in members if int(p["user_id"]) != int(current_user)]
    editing = sum(1 for p in members if p.get("action") == "editing") if include_activity else 0
    return {
        "online": len(members),
        "editing": editing,
        "peers": peers,
        "members": members,
    }
