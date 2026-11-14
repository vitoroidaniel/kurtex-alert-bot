"""Versioned shared Live Board order, stored in the existing SQLite database."""
from __future__ import annotations
import json
import re
from backend.storage import db

KEY = "workspace_manual_order_v1"
LANE = re.compile(r"^agent_workflow:(?:agent:[0-9]{1,20}|stage:(?:jbs_plant|meijer|dealer_shop|monitoring|to_yard|at_yard))$")

class OrderConflict(Exception):
    """The client has a stale board-order version."""

def read_order(data_dir=None):
    with db.read_conn(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (KEY,)).fetchone()
    if not row:
        return {"version": 0, "groups": {}}
    try:
        groups = json.loads(row["data_json"])
    except (ValueError, TypeError):
        groups = {}
    return {"version": int(row["version"]), "groups": groups if isinstance(groups, dict) else {}}

def save_order(group, ids, expected_version, actor_id, *, manager=False, data_dir=None):
    if not isinstance(group, str) or not LANE.fullmatch(group):
        raise ValueError("Invalid board column.")
    if not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(i, str) or not (1 <= len(i) <= 100) for i in ids):
        raise ValueError("Choose up to 500 valid case IDs.")
    if len(ids) != len(set(ids)):
        raise ValueError("The card order has repeated case IDs.")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
        raise ValueError("Invalid order version.")
    if not manager and group != f"agent_workflow:agent:{actor_id}":
        raise PermissionError("Only a lead can reorder other agents' or workflow columns.")
    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (KEY,)).fetchone()
        version = int(row["version"]) if row else 0
        if version != expected_version:
            raise OrderConflict("Another agent changed the board order. Refresh and retry.")
        groups = json.loads(row["data_json"]) if row else {}
        if not isinstance(groups, dict):
            groups = {}
        if ids:
            rows = conn.execute(
                "SELECT id,agent_id,status,closed_at,data_json FROM cases WHERE id IN (" +
                ",".join("?" for _ in ids) + ")", ids
            ).fetchall()
            if len(rows) != len(ids):
                raise ValueError("A case was removed or is no longer available. Refresh the board.")
            required_stage = group[len("agent_workflow:stage:"):] if group.startswith("agent_workflow:stage:") else None
            required_agent = group[len("agent_workflow:agent:"):] if group.startswith("agent_workflow:agent:") else None
            for case in rows:
                try:
                    info = json.loads(case["data_json"])
                except (TypeError, ValueError):
                    raise ValueError("A case could not be validated. Refresh the board.")
                if case["status"] != "reported" or info.get("closed_at") or case["closed_at"]:
                    raise ValueError("The board contains a closed or inactive case. Refresh and retry.")
                stage = str(info.get("board_stage") or "")
                if required_stage and stage != required_stage:
                    raise ValueError("A case moved to another column. Refresh and retry.")
                if required_agent and (stage or str(case["agent_id"]) != required_agent):
                    raise ValueError("A case moved to another agent. Refresh and retry.")
        groups[group] = ids
        next_version = version + 1
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (KEY, next_version, json.dumps(groups, separators=(",", ":")))
        )
        db.bump_version(conn, "cases_version")
    return {"version": next_version, "groups": groups}
