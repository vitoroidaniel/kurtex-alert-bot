"""Persistent SQLite storage for Kurtex Smart Lists on the backend DATA_DIR volume."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

DEFAULTS = [
    (
        "equipment_campaigns",
        "Equipment Campaigns",
        [
            {"key": "unit", "label": "Unit #", "type": "text"},
            {"key": "equipment", "label": "Equipment", "type": "select", "options": ["Truck", "Trailer"]},
            {"key": "part", "label": "Part / campaign", "type": "text"},
            {"key": "status", "label": "Status", "type": "select", "options": ["Pending", "In Progress", "Completed"]},
            {"key": "date", "label": "Date", "type": "date"},
            {"key": "comment", "label": "Comment", "type": "text"},
        ],
    ),
    (
        "parts_history",
        "Parts History",
        [
            {"key": "unit", "label": "Unit #", "type": "text"},
            {"key": "equipment", "label": "Equipment", "type": "select", "options": ["Truck", "Trailer"]},
            {"key": "part", "label": "Part replaced", "type": "text"},
            {"key": "shop", "label": "Shop", "type": "text"},
            {"key": "date", "label": "Date", "type": "date"},
            {"key": "cost", "label": "Cost", "type": "number"},
        ],
    ),
    (
        "bad_drivers",
        "Bad Drivers",
        [
            {"key": "driver", "label": "Driver", "type": "text"},
            {"key": "unit", "label": "Unit #", "type": "text"},
            {"key": "reason", "label": "Reason", "type": "text"},
            {"key": "date", "label": "Date", "type": "date"},
            {"key": "notes", "label": "Notes", "type": "text"},
        ],
    ),
]

_INIT_LOCK = threading.Lock()
_INIT_PATHS: set[str] = set()


def _db_path() -> str:
    explicit = os.getenv("KURTEX_LISTS_DB", "").strip()
    if explicit:
        return explicit
    env_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    base = env_dir if env_dir != Path("/app/data") else DATA_DIR
    return str(base / "kurtex-smart-lists.sqlite3")


@contextmanager
def _db():
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        with _INIT_LOCK:
            if path not in _INIT_PATHS:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS lists (id TEXT PRIMARY KEY, name TEXT NOT NULL, fields TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, list_id TEXT NOT NULL, values_json TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(list_id) REFERENCES lists(id))"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS entry_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id TEXT, list_id TEXT, actor TEXT, action TEXT, values_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_list_updated ON entries(list_id, updated_at DESC)")
                for ident, name, fields in DEFAULTS:
                    conn.execute(
                        "INSERT OR IGNORE INTO lists(id,name,fields,created_by) VALUES(?,?,?,?)",
                        (ident, name, json.dumps(fields), "system"),
                    )
                conn.commit()
                _INIT_PATHS.add(path)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _validate_values(fields: list[dict], values: object) -> dict[str, str]:
    if not isinstance(values, dict):
        raise ValueError("Values must be an object")
    allowed = {f["key"]: f for f in fields}
    if set(values) - set(allowed):
        raise ValueError("Unknown fields")
    cleaned: dict[str, str] = {}
    for key, val in values.items():
        f = allowed[key]
        if not isinstance(val, (str, int, float, bool)) and val is not None:
            raise ValueError("Invalid value")
        sval = str(val if val is not None else "")[:2000]
        if f["type"] == "select" and sval and sval not in f.get("options", []):
            raise ValueError("Invalid selection")
        if f["type"] == "number" and sval:
            try:
                float(sval)
            except ValueError as exc:
                raise ValueError("Invalid number") from exc
        cleaned[key] = sval
    return cleaned


def list_all() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT l.*, (SELECT COUNT(*) FROM entries e WHERE e.list_id=l.id) AS count FROM lists l WHERE archived=0 ORDER BY name").fetchall()
        return [{"id": r["id"], "name": r["name"], "fields": json.loads(r["fields"]), "count": r["count"]} for r in rows]


def create_list(name: str, fields: list[dict], user_id: str) -> str:
    if not name or not isinstance(fields, list) or not 1 <= len(fields) <= 25:
        raise ValueError("Name and 1-25 fields required")
    valid = []
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            raise ValueError("Invalid field")
        typ = f.get("type")
        label = str(f.get("label", "")).strip()[:60]
        if typ not in ("text", "number", "date", "select", "checkbox") or not label:
            raise ValueError("Invalid field type or label")
        opts = [str(v)[:80] for v in f.get("options", [])[:40]] if typ == "select" and isinstance(f.get("options", []), list) else []
        valid.append({"key": f"f{i}", "label": label, "type": typ, "options": opts})
    ident = secrets.token_hex(12)
    with _db() as conn:
        conn.execute(
            "INSERT INTO lists(id,name,fields,created_by) VALUES(?,?,?,?)",
            (ident, name.strip()[:90], json.dumps(valid), str(user_id)),
        )
        conn.execute("INSERT INTO entry_audit(list_id,actor,action,values_json) VALUES(?,?,?,?)", (ident, str(user_id), "list_create", json.dumps({"name": name, "fields": valid})))
    return ident


def get_list_with_entries(list_id: str) -> tuple[dict | None, list[dict]]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM lists WHERE id=? AND archived=0", (list_id,)).fetchone()
        if not row:
            return None, []
        entries = conn.execute(
            "SELECT * FROM entries WHERE list_id=? ORDER BY updated_at DESC,id DESC", (list_id,)
        ).fetchall()
        return (
            {"id": row["id"], "name": row["name"], "fields": json.loads(row["fields"]), "count": len(entries)},
            [{"id": e["id"], "values": json.loads(e["values_json"]), "updated_at": e["updated_at"]} for e in entries],
        )


def archive_list(list_id: str, user_id: str = "") -> str:
    if list_id in {ident for ident, _, _ in DEFAULTS}:
        return "builtin"
    with _db() as conn:
        row = conn.execute("SELECT id,name,fields FROM lists WHERE id=? AND archived=0", (list_id,)).fetchone()
        if not row:
            return "not_found"
        conn.execute("UPDATE lists SET archived=1 WHERE id=?", (list_id,))
        conn.execute("INSERT INTO entry_audit(list_id,actor,action,values_json) VALUES(?,?,?,?)", (list_id, str(user_id), "list_delete", json.dumps({"name": row["name"], "fields": json.loads(row["fields"])})))
    return "ok"


def upsert_entry(list_id: str, values: object, user_id: str, entry_id: str | None = None) -> tuple[str, str]:
    with _db() as conn:
        row = conn.execute("SELECT fields FROM lists WHERE id=? AND archived=0", (list_id,)).fetchone()
        if not row:
            return "list_not_found", ""
        if entry_id and not conn.execute("SELECT 1 FROM entries WHERE id=? AND list_id=?", (entry_id, list_id)).fetchone():
            return "entry_not_found", ""
        cleaned = _validate_values(json.loads(row["fields"]), values)
        if entry_id:
            conn.execute(
                "UPDATE entries SET values_json=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND list_id=?",
                (json.dumps(cleaned), str(user_id), entry_id, list_id),
            )
            action = "update"
        else:
            entry_id = secrets.token_hex(12)
            conn.execute(
                "INSERT INTO entries(id,list_id,values_json,updated_by) VALUES(?,?,?,?)",
                (entry_id, list_id, json.dumps(cleaned), str(user_id)),
            )
            action = "create"
        conn.execute(
            "INSERT INTO entry_audit(entry_id,list_id,actor,action,values_json) VALUES(?,?,?,?,?)",
            (entry_id, list_id, str(user_id), action, json.dumps(cleaned)),
        )
        return "ok", entry_id


def delete_entry(list_id: str, entry_id: str, user_id: str) -> str:
    with _db() as conn:
        row = conn.execute("SELECT 1 FROM lists WHERE id=? AND archived=0", (list_id,)).fetchone()
        if not row:
            return "list_not_found"
        old = conn.execute("SELECT values_json FROM entries WHERE id=? AND list_id=?", (entry_id, list_id)).fetchone()
        if not old:
            return "entry_not_found"
        conn.execute("DELETE FROM entries WHERE id=? AND list_id=?", (entry_id, list_id))
        conn.execute(
            "INSERT INTO entry_audit(entry_id,list_id,actor,action,values_json) VALUES(?,?,?,?,?)",
            (entry_id, list_id, str(user_id), "delete", old["values_json"]),
        )
        return "ok"


def audit_history(limit=50, before=0):
    with _db() as conn:
        rows = conn.execute("SELECT a.*, l.name AS list_name FROM entry_audit a LEFT JOIN lists l ON l.id=a.list_id WHERE (?=0 OR a.id<?) ORDER BY a.id DESC LIMIT ?", (before,before,limit+1)).fetchall()
        return [{"id": r["id"], "entry_id": r["entry_id"], "list_id": r["list_id"], "list_name": r["list_name"], "actor": r["actor"], "action": r["action"], "values": json.loads(r["values_json"] or "{}"), "created_at": r["created_at"]} for r in rows]

def bulk_entries(list_id, rows, user_id):
    if not isinstance(rows,list) or not 1 <= len(rows) <= 2000:
        raise ValueError("Provide 1–2,000 rows")
    with _db() as conn:
        row = conn.execute("SELECT fields FROM lists WHERE id=? AND archived=0",(list_id,)).fetchone()
        if not row:
            return None
        fields = json.loads(row["fields"])
        values = [_validate_values(fields, item.get("values",item) if isinstance(item,dict) else item) for item in rows]
        ids = []
        for value in values:
            eid = secrets.token_hex(12)
            ids.append(eid)
            data = json.dumps(value)
            conn.execute("INSERT INTO entries(id,list_id,values_json,updated_by) VALUES(?,?,?,?)",(eid,list_id,data,str(user_id)))
            conn.execute("INSERT INTO entry_audit(entry_id,list_id,actor,action,values_json) VALUES(?,?,?,?,?)",(eid,list_id,str(user_id),"create",data))
        return ids

def update_list(list_id, body, user_id):
    with _db() as conn:
        row = conn.execute("SELECT * FROM lists WHERE id=? AND archived=0",(list_id,)).fetchone()
        if not row:
            return None
        name = str(body.get("name",row["name"])).strip()[:90]
        fields = json.loads(row["fields"])
        raw = body.get("fields",fields)
        if not name or not isinstance(raw,list) or not 1<=len(raw)<=25:
            raise ValueError("Name and 1–25 fields required")
        existing = {f["key"] for f in fields}
        used = set()
        valid = []
        for f in raw:
            if not isinstance(f,dict): raise ValueError("Invalid field")
            typ = f.get("type")
            label = str(f.get("label","")).strip()[:60]
            if typ not in ("text","number","date","select","checkbox") or not label: raise ValueError("Invalid field")
            key = str(f.get("key",""))
            if key not in existing or key in used:
                key = next("f"+str(i) for i in range(10000) if "f"+str(i) not in existing|used)
            used.add(key)
            opts = [str(v)[:80] for v in f.get("options",[])[:40]] if typ=="select" and isinstance(f.get("options"),list) else []
            valid.append({"key":key,"label":label,"type":typ,"options":opts})
        conn.execute("UPDATE lists SET name=?,fields=? WHERE id=?",(name,json.dumps(valid),list_id))
        conn.execute("INSERT INTO entry_audit(list_id,actor,action,values_json) VALUES(?,?,?,?)",(list_id,str(user_id),"list_update",json.dumps({"name":name,"fields":valid})))
        return {"id":list_id,"name":name,"fields":valid}
