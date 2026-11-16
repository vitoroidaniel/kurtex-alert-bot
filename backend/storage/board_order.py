"""Versioned shared Live Board order & custom workflow lanes in SQLite."""
from __future__ import annotations
import json
import re
from backend.core.app_time import utc_now
from backend.storage import db

KEY = "workspace_manual_order_v1"
CUSTOM_LANES_KEY = "workspace_custom_lanes_v1"
LANE_LABELS_KEY = "workspace_lane_labels_v1"
BUILTIN_STAGES = ("jbs_plant", "meijer", "dealer_shop", "monitoring", "to_yard", "at_yard", "test")
SPECIAL_LANES = ("unassigned", "done")
AGENT_LANE_RE = re.compile(r"^agent_workflow:agent:[0-9]{1,20}$")


class OrderConflict(Exception):
    """The client has a stale board-order version."""


def _sanitize_custom_lanes(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    seen = set(BUILTIN_STAGES)
    for item in raw:
        if not isinstance(item, dict):
            continue
        lid = re.sub(r"[^a-z0-9_]", "_", str(item.get("id") or "").strip().lower()).strip("_")[:40]
        name = " ".join(str(item.get("name") or "").split()).strip()[:40]
        if not lid or not name or lid in seen:
            continue
        seen.add(lid)
        color = str(item.get("color") or "#7c3aed").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#7c3aed"
        out.append({
            "id": lid,
            "name": name,
            "color": color,
            "created_by": str(item.get("created_by") or "")[:64],
            "created_at": str(item.get("created_at") or "")[:40],
        })
        if len(out) >= 25:
            break
    return out


def read_custom_lanes(data_dir=None) -> list[dict]:
    with db.read_conn(data_dir) as conn:
        row = conn.execute("SELECT data_json FROM kv_state WHERE key=?", (CUSTOM_LANES_KEY,)).fetchone()
    if not row:
        return []
    try:
        return _sanitize_custom_lanes(json.loads(row["data_json"]))
    except (ValueError, TypeError):
        return []


def allowed_stages(data_dir=None) -> set[str]:
    return {"", *BUILTIN_STAGES, *(x["id"] for x in read_custom_lanes(data_dir))}


def add_custom_lane(name: str, actor_id, color: str = "#7c3aed", data_dir=None) -> dict:
    clean_name = " ".join(str(name or "").split()).strip()[:40]
    if not clean_name:
        raise ValueError("Enter a lane name.")
    slug = re.sub(r"[^a-z0-9]+", "_", clean_name.lower()).strip("_")[:28] or "lane"
    safe_color = str(color or "#7c3aed").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", safe_color):
        safe_color = "#7c3aed"
    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (CUSTOM_LANES_KEY,)).fetchone()
        version = int(row["version"]) if row else 0
        existing = _sanitize_custom_lanes(json.loads(row["data_json"])) if row else []
        if len(existing) >= 25:
            raise ValueError("Custom lane limit reached (25). Delete an unused lane first.")
        used = {"", *BUILTIN_STAGES, *(x["id"] for x in existing)}
        base = f"custom_{slug}"[:36]
        candidate = base
        idx = 2
        while candidate in used:
            candidate = f"{base[:32]}_{idx}"
            idx += 1
        entry = {
            "id": candidate,
            "name": clean_name,
            "color": safe_color,
            "created_by": str(actor_id or ""),
            "created_at": utc_now().isoformat(timespec="seconds"),
        }
        existing.append(entry)
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (CUSTOM_LANES_KEY, version + 1, json.dumps(existing, ensure_ascii=False, separators=(",", ":")))
        )
        db.bump_version(conn, "cases_version")
    return {"lane": entry, "custom_lanes": existing}


def delete_custom_lane(lane_id: str, data_dir=None) -> list[dict]:
    clean_id = str(lane_id or "").strip()
    if clean_id in BUILTIN_STAGES or not clean_id:
        raise ValueError("Built-in workflow lanes cannot be deleted.")
    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (CUSTOM_LANES_KEY,)).fetchone()
        version = int(row["version"]) if row else 0
        existing = _sanitize_custom_lanes(json.loads(row["data_json"])) if row else []
        remaining = [x for x in existing if x["id"] != clean_id]
        if len(remaining) == len(existing):
            raise LookupError("Custom lane not found.")
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (CUSTOM_LANES_KEY, version + 1, json.dumps(remaining, ensure_ascii=False, separators=(",", ":")))
        )
        # Re-home any active or archived cases in this stage back to Assigned Agent ("")
        case_rows = conn.execute(
            "SELECT id, data_json FROM cases WHERE board_stage=?",
            (clean_id,),
        ).fetchall()
        for crow in case_rows:
            try:
                cdict = json.loads(crow["data_json"])
            except Exception:
                continue
            if isinstance(cdict, dict) and str(cdict.get("board_stage") or "") == clean_id:
                cdict["board_stage"] = ""
                conn.execute(
                    "UPDATE cases SET board_stage='', data_json=? WHERE id=?",
                    (json.dumps(cdict, ensure_ascii=False, separators=(",", ":")), crow["id"]),
                )
        ord_row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (KEY,)).fetchone()
        if ord_row:
            try:
                groups = json.loads(ord_row["data_json"])
            except Exception:
                groups = {}
            if isinstance(groups, dict) and f"agent_workflow:stage:{clean_id}" in groups:
                groups.pop(f"agent_workflow:stage:{clean_id}", None)
                conn.execute(
                    "UPDATE kv_state SET version=?, data_json=? WHERE key=?",
                    (int(ord_row["version"]) + 1, json.dumps(groups, separators=(",", ":")), KEY),
                )
        db.bump_version(conn, "cases_version")
    return remaining


def read_lane_labels(data_dir=None) -> dict[str, str]:
    """Return custom display titles for built-in workflow, unassigned, and done lanes."""
    with db.read_conn(data_dir) as conn:
        row = conn.execute("SELECT data_json FROM kv_state WHERE key=?", (LANE_LABELS_KEY,)).fetchone()
    if not row:
        return {}
    try:
        parsed = json.loads(row["data_json"])
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    valid_keys = set(BUILTIN_STAGES) | set(SPECIAL_LANES)
    return {
        str(k): " ".join(str(v or "").split()).strip()[:40]
        for k, v in parsed.items()
        if str(k) in valid_keys and " ".join(str(v or "").split()).strip()
    }


def rename_lane(lane_id: str, new_name: str, *, reset: bool = False, data_dir=None) -> dict:
    """Rename any custom or built-in workflow/unassigned/done lane."""
    raw_key = str(lane_id or "").strip()
    clean_key = raw_key[len("stage:"):] if raw_key.startswith("stage:") else raw_key
    clean_name = " ".join(str(new_name or "").split()).strip()[:40]
    if not reset and not clean_name:
        raise ValueError("Enter a lane name.")
    valid_builtin = set(BUILTIN_STAGES) | set(SPECIAL_LANES)

    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (CUSTOM_LANES_KEY,)).fetchone()
        custom_ver = int(row["version"]) if row else 0
        custom_lanes = _sanitize_custom_lanes(json.loads(row["data_json"])) if row else []
        custom_hit = next((x for x in custom_lanes if x["id"] == clean_key), None)

        if custom_hit:
            if reset or not clean_name:
                raise ValueError("Enter a new name for this custom lane.")
            custom_hit["name"] = clean_name
            conn.execute(
                "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
                (CUSTOM_LANES_KEY, custom_ver + 1, json.dumps(custom_lanes, ensure_ascii=False, separators=(",", ":")))
            )
            labels = read_lane_labels(data_dir)
            db.bump_version(conn, "cases_version")
            return {"lane_id": raw_key, "name": clean_name, "custom_lanes": custom_lanes, "lane_labels": labels}

        if clean_key not in valid_builtin:
            raise LookupError("Lane not found or cannot be renamed.")

        lbl_row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (LANE_LABELS_KEY,)).fetchone()
        lbl_ver = int(lbl_row["version"]) if lbl_row else 0
        try:
            labels = json.loads(lbl_row["data_json"]) if lbl_row else {}
        except (ValueError, TypeError):
            labels = {}
        if not isinstance(labels, dict):
            labels = {}
        if reset:
            labels.pop(clean_key, None)
        else:
            labels[clean_key] = clean_name
        clean_labels = {
            str(k): " ".join(str(v or "").split()).strip()[:40]
            for k, v in labels.items()
            if str(k) in valid_builtin and " ".join(str(v or "").split()).strip()
        }
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (LANE_LABELS_KEY, lbl_ver + 1, json.dumps(clean_labels, ensure_ascii=False, separators=(",", ":")))
        )
        db.bump_version(conn, "cases_version")
    return {"lane_id": raw_key, "name": clean_name if not reset else "", "custom_lanes": custom_lanes, "lane_labels": clean_labels}


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


def _is_valid_group(group: str, data_dir=None) -> bool:
    if not isinstance(group, str):
        return False
    if AGENT_LANE_RE.fullmatch(group):
        return True
    if group.startswith("agent_workflow:stage:"):
        stage = group[len("agent_workflow:stage:"):]
        return bool(stage) and stage in allowed_stages(data_dir)
    return False


def save_order(group, ids, expected_version, actor_id, *, manager=False, data_dir=None):
    if not _is_valid_group(group, data_dir):
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


def reset_order(data_dir=None) -> dict:
    """Clear manual card ordering across all lanes for Managers and Developers."""
    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version FROM kv_state WHERE key=?", (KEY,)).fetchone()
        next_version = (int(row["version"]) if row else 0) + 1
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (KEY, next_version, "{}"),
        )
        db.bump_version(conn, "cases_version")
    return {"version": next_version, "groups": {}}
