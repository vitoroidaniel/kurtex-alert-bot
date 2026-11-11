"""SQLite-backed case and alert storage with cross-process WAL concurrency.

Replaces full-file JSON rewrites and fcntl locks with indexed SQLite queries on
DATA_DIR/kurtex.sqlite3 while preserving every public function signature.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from backend.core.app_time import (
    chicago_date_str,
    parse_timestamp,
    today_str,
    utc_now,
    week_start_str,
)
from backend.storage import db

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CASES_FILE = DATA_DIR / "cases.json"
ALERTS_FILE = DATA_DIR / "active_alerts.json"
_READ_CACHE: dict[Path, tuple[tuple[int, int, int], object, bool]] = {}


def _storage_dir() -> Path:
    """Resolve active storage directory, honoring test overrides on CASES_FILE or DATA_DIR."""
    default_cases = Path(os.getenv("DATA_DIR", "/app/data")) / "cases.json"
    if CASES_FILE != default_cases and CASES_FILE.parent != DATA_DIR:
        CASES_FILE.parent.mkdir(parents=True, exist_ok=True)
        return CASES_FILE.parent
    env_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    if env_dir != DATA_DIR:
        env_dir.mkdir(parents=True, exist_ok=True)
        return env_dir
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _upsert_case_row(conn: sqlite3.Connection, case: dict) -> None:
    cols = db._extract_case_columns(case)
    conn.execute(
        """INSERT INTO cases(
            id, status, agent_id, opened_at, assigned_at, reported_at,
            closed_at, updated_at, report_msg_id, unit_number,
            driver_name, group_name, source, workspace_request_id,
            created_by_id, data_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            agent_id=excluded.agent_id,
            opened_at=excluded.opened_at,
            assigned_at=excluded.assigned_at,
            reported_at=excluded.reported_at,
            closed_at=excluded.closed_at,
            updated_at=excluded.updated_at,
            report_msg_id=excluded.report_msg_id,
            unit_number=excluded.unit_number,
            driver_name=excluded.driver_name,
            group_name=excluded.group_name,
            source=excluded.source,
            workspace_request_id=excluded.workspace_request_id,
            created_by_id=excluded.created_by_id,
            data_json=excluded.data_json""",
        cols,
    )
    db.bump_version(conn, "cases_version")


def _load(path: Path, *, strict: bool = False) -> list[dict]:
    if path == CASES_FILE:
        sdir = _storage_dir()
        try:
            with db.read_conn(sdir) as conn:
                rows = conn.execute("SELECT data_json FROM cases ORDER BY rowid ASC").fetchall()
                return [json.loads(r["data_json"]) for r in rows]
        except Exception as exc:
            logger.error("Failed to load cases from SQLite: %s", exc)
            if strict or path == CASES_FILE:
                raise
            return []
    if not path.exists():
        return []
    try:
        stat = path.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = _READ_CACHE.get(path)
        if cached is None or cached[0] != signature:
            value = json.loads(path.read_text(encoding="utf-8"))
            flat = isinstance(value, list) and all(
                isinstance(row, dict) and all(not isinstance(v, (list, dict)) for v in row.values())
                for row in value
            )
            _READ_CACHE[path] = (signature, value, flat)
        cached = _READ_CACHE[path]
        return [dict(row) for row in cached[1]] if cached[2] else copy.deepcopy(cached[1])
    except Exception as exc:
        logger.error("Failed to load %s: %s", path.name, exc)
        if strict:
            raise
        return []


def _save(path: Path, data: list[dict] | dict, *, strict: bool = False) -> None:
    if path == CASES_FILE:
        if not isinstance(data, list):
            raise ValueError("Case storage requires a list of case objects.")
        sdir = _storage_dir()
        with db.write_tx(sdir) as conn:
            conn.execute("DELETE FROM cases")
            for case in data:
                if isinstance(case, dict) and case.get("id"):
                    conn.execute(
                        """INSERT INTO cases(
                            id, status, agent_id, opened_at, assigned_at, reported_at,
                            closed_at, updated_at, report_msg_id, unit_number,
                            driver_name, group_name, source, workspace_request_id,
                            created_by_id, data_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        db._extract_case_columns(case),
                    )
            db.bump_version(conn, "cases_version")
        return

    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        payload = json.dumps(data, separators=(",", ":"), default=str)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
        _READ_CACHE.pop(path, None)
    except Exception as exc:
        logger.error("Failed to save %s: %s", path.name, exc)
        if strict:
            raise
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def now_iso() -> str:
    return utc_now().isoformat()


def get_cases_version() -> int:
    return db.get_version("cases_version", _storage_dir())


# ── Cases — write ─────────────────────────────────────────────────────────────

def create_case(
    case_id: str,
    driver_name: str,
    driver_username: Optional[str],
    group_name: str,
    description: str,
) -> dict:
    case = {
        "id":              case_id,
        "driver_name":     driver_name,
        "driver_username": driver_username,
        "group_name":      group_name,
        "description":     description,
        "opened_at":       now_iso(),
        "assigned_at":     None,
        "closed_at":       None,
        "agent_id":        None,
        "agent_name":      None,
        "agent_username":  None,
        "status":          "open",
        "notes":           None,
        "report_msg_id":   None,
    }
    with db.write_tx(_storage_dir()) as conn:
        _upsert_case_row(conn, case)
    logger.info("Case %s created", case_id)
    return case


def assign_case(
    case_id: str,
    agent_id: int,
    agent_name: str,
    agent_username: Optional[str],
    allow_reassign: bool = False,
) -> Optional[dict]:
    """Atomically claim an unassigned case, or explicitly reassign it."""
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            logger.warning("assign_case: %s not found", case_id)
            return None
        case = json.loads(row["data_json"])
        if case.get("status") == "done":
            return None
        previous_agent_id = case.get("agent_id")
        if previous_agent_id is not None and not allow_reassign:
            return None
        assigned_at = now_iso()
        opened_at_dt = parse_timestamp(case.get("opened_at"))
        assigned_at_dt = parse_timestamp(assigned_at)
        response_secs = (
            int((assigned_at_dt - opened_at_dt).total_seconds())
            if opened_at_dt and assigned_at_dt
            else None
        )
        case.update({
            "assigned_at":    assigned_at,
            "agent_id":       agent_id,
            "agent_name":     agent_name,
            "agent_username": agent_username,
            "status":         "assigned",
            "response_secs":  response_secs,
            "reassigned":     bool(previous_agent_id is not None),
        })
        _upsert_case_row(conn, case)
        logger.info("Case %s assigned to %s", case_id, agent_name)
        return dict(case)


def report_case(case_id: str, notes: Optional[str] = "case reported") -> Optional[dict]:
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            return None
        case = json.loads(row["data_json"])
        if case.get("status") == "done" or case.get("closed_at"):
            return case
        now = now_iso()
        case.update({"status": "reported", "notes": notes, "reported_at": now, "updated_at": now})
        _upsert_case_row(conn, case)
        return case


def save_maintenance_report(case_id: str, data: dict, report_text: str, media: list) -> Optional[dict]:
    """Persist report fields atomically without reopening a concurrently closed case."""
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            return None
        case = json.loads(row["data_json"])
        for field in (
            "vehicle_type", "unit_number", "load_type", "location", "priority",
            "pickup", "delivery", "comments", "setpoint", "current_temp", "temp_recorder",
        ):
            case[field] = data.get("load" if field == "load_type" else field, "")
        now = now_iso()
        case.update({
            "report_driver": data.get("driver", ""),
            "issue_text":    data.get("issue", ""),
            "report_text":   report_text,
            "report_data":   dict(data),
            "media":         media,
            "reported_at":   now,
            "updated_at":    now,
        })
        if case.get("status") != "done" and not case.get("closed_at"):
            case["status"] = "reported"
            if not case.get("notes"):
                case["notes"] = "case reported"
        _upsert_case_row(conn, case)
        return dict(case)


def _close_record(case: dict, notes: Optional[str] = None) -> dict:
    """Shared Telegram/API close transition. Repeated closes preserve history."""
    if case.get("status") == "done":
        return case
    closed_at = now_iso()
    assigned = parse_timestamp(case.get("assigned_at"))
    closed = parse_timestamp(closed_at)
    case.update({
        "closed_at":       closed_at,
        "updated_at":      closed_at,
        "status":          "done",
        "resolution_secs": max(0, int((closed - assigned).total_seconds())) if assigned and closed else None,
    })
    if notes is not None:
        case["notes"] = notes
    return case


def close_case(
    case_id: str,
    notes: Optional[str] = None,
    *,
    replaced_parts: Optional[list[str]] = None,
    parts_replaced: Optional[bool] = None,
) -> Optional[dict]:
    """Close atomically, retaining explicitly reported replaced part numbers for AI history."""
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            return None
        case = json.loads(row["data_json"])
        if case.get("status") == "done":
            return dict(case)
        if parts_replaced is not None:
            numbers = [str(part).strip() for part in (replaced_parts or [])]
            if len(numbers) > 20 or any(not number or len(number) > 80 for number in numbers):
                raise ValueError("Invalid replacement part numbers")
            case["parts_replaced"] = bool(parts_replaced)
            case["replaced_parts"] = numbers if parts_replaced else []
            case["replaced_part_numbers"] = ", ".join(case["replaced_parts"])
            case["parts_recorded_at"] = now_iso()
        _close_record(case, notes)
        _upsert_case_row(conn, case)
        conn.execute("DELETE FROM active_alerts WHERE alert_id=?", (case_id,))
        logger.info(
            "Case %s closed; replaced part numbers: %s",
            case_id,
            case.get("replaced_part_numbers") or "none/unspecified",
        )
        return dict(case)


def is_maintenance_report(case: dict) -> bool:
    return bool(
        case.get("status") == "reported"
        or case.get("report_data")
        or (case.get("unit_number") and case.get("issue_text"))
    )


def workspace_revision(case: dict) -> str:
    import hashlib
    from backend.core.case_snapshot import normalize
    return hashlib.sha256(json.dumps(normalize(case), sort_keys=True, default=str).encode()).hexdigest()


def workspace_write(actor: dict, payload: dict, case_id: Optional[str] = None) -> dict:
    """Authorized report writes under SQLite transaction isolation."""
    with db.write_tx(_storage_dir()) as conn:
        now = now_iso()
        if case_id is None:
            existing_row = conn.execute(
                "SELECT data_json FROM cases WHERE workspace_request_id=? AND created_by_id=?",
                (payload["request_id"], str(actor["id"])),
            ).fetchone()
            if existing_row:
                return json.loads(existing_row["data_json"])
            target = payload.get("target_agent") or {
                "id": actor["id"],
                "name": actor["name"],
                "username": actor.get("username", ""),
            }
            case = {
                "id":                   payload["id"],
                "driver_name":          payload["driver"],
                "driver_username":      "",
                "group_name":           payload.get("group", ""),
                "description":          payload["issue"],
                "opened_at":            now,
                "reported_at":          now,
                "updated_at":           now,
                "assigned_at":          now,
                "closed_at":            None,
                "agent_id":             target["id"],
                "agent_name":           target["name"],
                "agent_username":       target.get("username", ""),
                "status":               "reported",
                "vehicle_type":         payload["vehicle_type"],
                "unit_number":          payload["unit_number"],
                "report_driver":        payload["driver"],
                "issue_text":           payload["issue"],
                "location":             payload.get("location", ""),
                "priority":             payload["priority"],
                "load_type":            payload.get("load_type", ""),
                "pickup":               payload.get("pickup", ""),
                "delivery":             payload.get("delivery", ""),
                "setpoint":             payload.get("setpoint", ""),
                "current_temp":         payload.get("current_temp", ""),
                "temp_recorder":        payload.get("temp_recorder", ""),
                "part_number":          payload.get("part_number", ""),
                "comments":             payload.get("comments", ""),
                "notes":                None,
                "media":                [],
                "source":               "workspace",
                "created_by_id":        actor["id"],
                "workspace_request_id": payload["request_id"],
                "report_data":          {**payload, "load": payload.get("load_type", "")},
            }
        else:
            row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                raise LookupError("Case not found.")
            case = json.loads(row["data_json"])
            if payload["action"] not in ("reassign", "stage", "move") and not is_maintenance_report(case):
                raise LookupError("Maintenance report not found. Awaiting Telegram report.")
            if actor["role"] not in ("developer", "super_admin") and str(case.get("agent_id")) != str(actor["id"]):
                raise PermissionError("Only the assigned agent or a manager can update this case.")
            if payload["action"] == "close" and case.get("status") == "done":
                return case
            if payload.get("revision") != workspace_revision(case):
                raise ValueError("This report changed. Refresh its details before saving again.")
            if case.get("status") not in ("open", "assigned", "reported") or case.get("closed_at"):
                raise ValueError("This report is already closed.")
            if payload["action"] == "close":
                _close_record(case)
                case.update({"closed_by_id": actor["id"], "closed_by_name": actor["name"]})
                conn.execute("DELETE FROM active_alerts WHERE alert_id=?", (case_id,))
            elif payload["action"] == "note":
                case.setdefault("workspace_notes", []).append({
                    "text": payload["text"], "author": actor["name"], "created_at": now,
                })
            elif payload["action"] in ("note_edit", "note_delete"):
                notes = case.setdefault("workspace_notes", [])
                index = payload["index"]
                if index < 0 or index >= len(notes):
                    raise ValueError("Note no longer exists. Refresh the case.")
                if payload["action"] == "note_edit":
                    notes[index]["text"] = payload["text"]
                    notes[index]["edited_at"] = now
                else:
                    notes.pop(index)
            elif payload["action"] in ("reassign", "move"):
                target = payload["target"]
                if str(case.get("agent_id")) == str(target["id"]) and (
                    payload["action"] != "move" or (case.get("board_stage") or "") == payload["stage"]
                ):
                    return dict(case)
                previous = case.get("agent_name") or "Unassigned"
                owner_changed = str(case.get("agent_id")) != str(target["id"])
                if owner_changed:
                    case.update({
                        "agent_id": target["id"],
                        "agent_name": target["name"],
                        "agent_username": target.get("username", ""),
                        "assigned_at": now,
                        "reassigned": True,
                    })
                    if case.get("status") == "open":
                        case["status"] = "assigned"
                    case.setdefault("workspace_history", []).append({
                        "action": "reassign", "author": actor["name"],
                        "from": previous, "to": target["name"], "created_at": now,
                    })
                if payload["action"] == "move" and (case.get("board_stage") or "") != payload["stage"]:
                    old_stage = case.get("board_stage") or ""
                    case["board_stage"] = payload["stage"]
                    case.setdefault("workspace_history", []).append({
                        "action": "stage", "author": actor["name"],
                        "from": old_stage, "to": payload["stage"], "created_at": now,
                    })
            elif payload["action"] == "stage":
                previous = case.get("board_stage") or ""
                case["board_stage"] = payload["stage"]
                case.setdefault("workspace_history", []).append({
                    "action": "stage", "author": actor["name"],
                    "from": previous, "to": payload["stage"], "created_at": now,
                })
            elif payload["action"] == "edit":
                fields = dict(payload["fields"])
                new_load = fields.get(
                    "load_type",
                    case.get("load_type") or (case.get("report_data") or {}).get("load", ""),
                )
                new_equipment = fields.get("vehicle_type", case.get("vehicle_type", ""))
                if new_load == "Empty":
                    fields.update({key: "" for key in ("pickup", "delivery", "setpoint", "current_temp", "temp_recorder")})
                elif new_load == "" or new_equipment != "reefer":
                    fields.update({key: "" for key in ("setpoint", "current_temp", "temp_recorder")})
                case.update(fields)
                if isinstance(case.get("report_data"), dict):
                    for key, value in fields.items():
                        report_key = {"report_driver": "driver", "load_type": "load", "group_name": "group"}.get(key, key)
                        if report_key in case["report_data"]:
                            case["report_data"][report_key] = value
            case["updated_at"] = now
            if payload["action"] not in ("reassign", "stage", "move"):
                case.setdefault("workspace_history", []).append({
                    "action": payload["action"], "author": actor["name"], "created_at": now,
                })
        _upsert_case_row(conn, case)
        return dict(case)


def mark_missed(case_id: str) -> None:
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            return
        case = json.loads(row["data_json"])
        if case.get("status") == "open" and case.get("agent_id") is None:
            case["status"] = "missed"
            case["updated_at"] = now_iso()
            _upsert_case_row(conn, case)


def set_report_msg_id(case_id: str, msg_id: int) -> None:
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            return
        case = json.loads(row["data_json"])
        case["report_msg_id"] = msg_id
        _upsert_case_row(conn, case)


# ── Cases — indexed reads ─────────────────────────────────────────────────────

def get_case(case_id: str) -> Optional[dict]:
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        return json.loads(row["data_json"]) if row else None


def get_cases_for_agent_today(agent_id: int) -> list[dict]:
    today = today_str()
    cutoff = (utc_now() - timedelta(days=2)).isoformat()
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            "SELECT data_json FROM cases WHERE agent_id=? AND assigned_at IS NOT NULL AND assigned_at >= ? ORDER BY assigned_at DESC",
            (int(agent_id), cutoff),
        ).fetchall()
    return [c for c in (json.loads(r["data_json"]) for r in rows) if chicago_date_str(c.get("assigned_at")) == today]


def get_all_cases_for_agent(agent_id: int) -> list[dict]:
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            "SELECT data_json FROM cases WHERE agent_id=? ORDER BY opened_at DESC",
            (int(agent_id),),
        ).fetchall()
    return [json.loads(r["data_json"]) for r in rows]


def get_active_cases_for_agent(
    agent_id: int,
    status_filter: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[dict]:
    """Indexed lookup of active cases for one agent (zero closed-history scan)."""
    if status_filter == "assigned":
        statuses = ("assigned",)
    elif status_filter == "reported":
        statuses = ("reported",)
    else:
        statuses = ("assigned", "reported")
    placeholders = ",".join("?" * len(statuses))
    sql = f"SELECT data_json FROM cases WHERE agent_id=? AND status IN ({placeholders}) ORDER BY opened_at DESC"
    params: list = [int(agent_id), *statuses]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([int(limit), max(0, int(offset))])
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [json.loads(r["data_json"]) for r in rows]


def count_active_cases_for_agent(agent_id: int) -> dict[str, int]:
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM cases WHERE agent_id=? AND status IN ('assigned', 'reported') GROUP BY status",
            (int(agent_id),),
        ).fetchall()
    counts = {"assigned": 0, "reported": 0}
    for r in rows:
        counts[r["status"]] = int(r["cnt"])
    counts["total"] = counts["assigned"] + counts["reported"]
    return counts


def get_agent_cases_page_data(
    agent_id: int,
    status_filter: Optional[str] = None,
    page: int = 0,
    per_page: int = 5,
) -> tuple[dict[str, int], str, int, int, list[dict]]:
    """Fetch counts + paginated active cases for /mycases in one SQLite read."""
    flt = status_filter if status_filter in ("assigned", "reported") else "all"
    per_page = max(1, int(per_page))
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM cases WHERE agent_id=? AND status IN ('assigned', 'reported') GROUP BY status",
            (int(agent_id),),
        ).fetchall()
        counts = {"assigned": 0, "reported": 0}
        for r in rows:
            counts[r["status"]] = int(r["cnt"])
        counts["total"] = counts["assigned"] + counts["reported"]
        if counts["total"] == 0:
            return counts, flt, 1, 0, []

        filtered_total = counts["total"] if flt == "all" else counts[flt]
        if filtered_total == 0:
            flt = "all"
            filtered_total = counts["total"]

        total_pages = max(1, ((filtered_total - 1) // per_page) + 1)
        clamped_page = max(0, min(int(page), total_pages - 1))
        offset = clamped_page * per_page

        statuses = ("assigned", "reported") if flt == "all" else (flt,)
        placeholders = ",".join("?" * len(statuses))
        batch_rows = conn.execute(
            f"SELECT data_json FROM cases WHERE agent_id=? AND status IN ({placeholders}) ORDER BY opened_at DESC LIMIT ? OFFSET ?",
            [int(agent_id), *statuses, per_page, offset],
        ).fetchall()
    return counts, flt, total_pages, clamped_page, [json.loads(r["data_json"]) for r in batch_rows]


def get_case_or_prefix(case_id: str) -> tuple[Optional[dict], bool]:
    """Resolve an exact case ID or unique prefix directly in SQLite (returns (case, ambiguous))."""
    cid = (case_id or "").strip()
    if not cid:
        return None, False
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (cid,)).fetchone()
        if row:
            return json.loads(row["data_json"]), False
        escaped = cid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = conn.execute(
            "SELECT data_json FROM cases WHERE id LIKE ? ESCAPE '\\' LIMIT 2",
            (escaped,),
        ).fetchall()
    if not rows:
        return None, False
    if len(rows) > 1:
        return None, True
    return json.loads(rows[0]["data_json"]), False


def get_active_case_for_agent(agent_id: int) -> Optional[dict]:
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute(
            "SELECT data_json FROM cases WHERE agent_id=? AND status IN ('assigned', 'reported') ORDER BY rowid DESC LIMIT 1",
            (int(agent_id),),
        ).fetchone()
    return json.loads(row["data_json"]) if row else None


def get_cases_today() -> list[dict]:
    today = today_str()
    cutoff = (utc_now() - timedelta(days=2)).isoformat()
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            "SELECT data_json FROM cases WHERE opened_at >= ? ORDER BY opened_at DESC",
            (cutoff,),
        ).fetchall()
    return [c for c in (json.loads(r["data_json"]) for r in rows) if chicago_date_str(c.get("opened_at")) == today]


def get_cases_between(start, end) -> list[dict]:
    """Return cases opened in the half-open UTC-aware interval [start, end)."""
    cutoff = (start - timedelta(hours=2)).isoformat()
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            "SELECT data_json FROM cases WHERE opened_at >= ? ORDER BY opened_at DESC",
            (cutoff,),
        ).fetchall()
    result = []
    for r in rows:
        case = json.loads(r["data_json"])
        opened = parse_timestamp(case.get("opened_at"))
        if opened and start <= opened < end:
            result.append(case)
    return result


def get_cases_this_week() -> list[dict]:
    start = week_start_str()
    cutoff = (utc_now() - timedelta(days=9)).isoformat()
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            "SELECT data_json FROM cases WHERE opened_at >= ? ORDER BY opened_at DESC",
            (cutoff,),
        ).fetchall()
    return [c for c in (json.loads(r["data_json"]) for r in rows) if chicago_date_str(c.get("opened_at")) >= start]


def get_all_cases() -> list[dict]:
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute("SELECT data_json FROM cases ORDER BY opened_at DESC").fetchall()
    return [json.loads(r["data_json"]) for r in rows]


def get_workspace_board_cases(min_reported_date: str = "2026-10-12") -> list[dict]:
    """Fast indexed read of active reported cases for Case Workspace board."""
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            """SELECT data_json FROM cases
               WHERE status='reported' AND (closed_at IS NULL OR closed_at='')
               ORDER BY COALESCE(reported_at, opened_at) DESC""",
        ).fetchall()
    result = []
    for r in rows:
        c = json.loads(r["data_json"])
        if is_maintenance_report(c) and (chicago_date_str(c.get("reported_at")) or "") >= min_reported_date:
            result.append(c)
    return result


def get_untouched_unassigned_cases() -> list[dict]:
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            """SELECT data_json FROM cases
               WHERE status='open' AND agent_id IS NULL
                 AND assigned_at IS NULL AND closed_at IS NULL
                 AND report_msg_id IS NULL
               ORDER BY opened_at DESC""",
        ).fetchall()
    return [c for c in (json.loads(r["data_json"]) for r in rows) if c.get("notes") is None]


# ── Active alerts — SQLite-backed single-row + bulk sync ──────────────────────

def _serialize_alert_record(record: dict) -> dict:
    r = dict(record)
    if isinstance(r.get("created_at"), datetime):
        r["created_at"] = r["created_at"].isoformat()
    if isinstance(r.get("last_escalated_at"), datetime):
        r["last_escalated_at"] = r["last_escalated_at"].isoformat()
    return r


def upsert_active_alert(alert_id: str, record: dict) -> None:
    rec = _serialize_alert_record(record)
    tb = rec.get("taken_by")
    tb_id = int(tb[0]) if isinstance(tb, (list, tuple)) and tb else None
    with db.write_tx(_storage_dir()) as conn:
        conn.execute(
            """INSERT INTO active_alerts(alert_id, created_at, taken_by_id, data_json, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(alert_id) DO UPDATE SET
                   created_at=excluded.created_at,
                   taken_by_id=excluded.taken_by_id,
                   data_json=excluded.data_json,
                   updated_at=excluded.updated_at""",
            (
                str(alert_id),
                str(rec.get("created_at") or ""),
                tb_id,
                json.dumps(rec, separators=(",", ":"), default=str),
                now_iso(),
            ),
        )


def delete_active_alert(alert_id: str) -> None:
    with db.write_tx(_storage_dir()) as conn:
        conn.execute("DELETE FROM active_alerts WHERE alert_id=?", (str(alert_id),))


def save_active_alerts(alerts: dict) -> None:
    now = now_iso()
    with db.write_tx(_storage_dir()) as conn:
        conn.execute("DELETE FROM active_alerts")
        for aid, record in alerts.items():
            rec = _serialize_alert_record(record)
            tb = rec.get("taken_by")
            tb_id = int(tb[0]) if isinstance(tb, (list, tuple)) and tb else None
            conn.execute(
                """INSERT INTO active_alerts(alert_id, created_at, taken_by_id, data_json, updated_at)
                   VALUES(?,?,?,?,?)""",
                (
                    str(aid),
                    str(rec.get("created_at") or ""),
                    tb_id,
                    json.dumps(rec, separators=(",", ":"), default=str),
                    now,
                ),
            )


def load_active_alerts() -> dict:
    """Load persisted open/non-done alerts in one indexed JOIN (zero O(K*N) loops)."""
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            """SELECT a.alert_id, a.data_json
               FROM active_alerts a
               LEFT JOIN cases c ON c.id = a.alert_id
               WHERE c.status IS NULL OR c.status != 'done'""",
        ).fetchall()
    return {r["alert_id"]: json.loads(r["data_json"]) for r in rows}


def prune_done_active_alerts() -> int:
    with db.write_tx(_storage_dir()) as conn:
        cur = conn.execute(
            "DELETE FROM active_alerts WHERE alert_id IN (SELECT id FROM cases WHERE status='done')"
        )
        return cur.rowcount


# ── Async wrappers ────────────────────────────────────────────────────────────

async def async_get_active_case_for_agent(agent_id):
    return await asyncio.to_thread(get_active_case_for_agent, agent_id)

async def async_get_active_cases_for_agent(agent_id, status_filter=None, limit=None, offset=0):
    return await asyncio.to_thread(get_active_cases_for_agent, agent_id, status_filter, limit, offset)

async def async_count_active_cases_for_agent(agent_id):
    return await asyncio.to_thread(count_active_cases_for_agent, agent_id)

async def async_get_agent_cases_page_data(agent_id, status_filter=None, page=0, per_page=5):
    return await asyncio.to_thread(get_agent_cases_page_data, agent_id, status_filter, page, per_page)

async def async_create_case(case_id, driver_name, driver_username, group_name, description):
    return await asyncio.to_thread(
        create_case, case_id, driver_name, driver_username, group_name, description
    )

async def async_assign_case(case_id, agent_id, agent_name, agent_username, allow_reassign=False):
    return await asyncio.to_thread(
        assign_case, case_id, agent_id, agent_name, agent_username, allow_reassign
    )

async def async_save_maintenance_report(case_id, data, report_text, media):
    return await asyncio.to_thread(save_maintenance_report, case_id, data, report_text, media)

async def async_close_case(case_id, notes=None, *, replaced_parts=None, parts_replaced=None):
    return await asyncio.to_thread(
        close_case, case_id, notes, replaced_parts=replaced_parts, parts_replaced=parts_replaced
    )

async def async_mark_missed(case_id):
    return await asyncio.to_thread(mark_missed, case_id)

async def async_get_case(case_id):
    return await asyncio.to_thread(get_case, case_id)

async def async_get_cases_for_agent_today(agent_id):
    return await asyncio.to_thread(get_cases_for_agent_today, agent_id)

async def async_get_all_cases_for_agent(agent_id):
    return await asyncio.to_thread(get_all_cases_for_agent, agent_id)

async def async_get_cases_today():
    return await asyncio.to_thread(get_cases_today)

async def async_get_cases_between(start, end):
    return await asyncio.to_thread(get_cases_between, start, end)

async def async_get_cases_this_week():
    return await asyncio.to_thread(get_cases_this_week)

async def async_get_untouched_unassigned_cases():
    return await asyncio.to_thread(get_untouched_unassigned_cases)

async def async_set_report_msg_id(case_id, msg_id):
    return await asyncio.to_thread(set_report_msg_id, case_id, msg_id)

async def async_upsert_active_alert(alert_id, record):
    return await asyncio.to_thread(upsert_active_alert, alert_id, record)

async def async_delete_active_alert(alert_id):
    return await asyncio.to_thread(delete_active_alert, alert_id)

async def ensure_indexes():
    await asyncio.to_thread(get_cases_version)


# ── Workspace attachments & media ─────────────────────────────────────────────

def workspace_attachment_write(actor, case_id, attachment=None, remove_id=None):
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        case = json.loads(row["data_json"]) if row else None
        if not case or not is_maintenance_report(case):
            raise LookupError("Case not found.")
        if actor["role"] not in ("developer", "super_admin") and str(case.get("agent_id")) != str(actor["id"]):
            raise PermissionError("You cannot manage this case.")
        if not remove_id and (case.get("status") not in ("open", "assigned", "reported") or case.get("closed_at")):
            raise ValueError("Cannot upload to a closed case.")
        items = case.setdefault("workspace_attachments", [])
        removed = None
        if remove_id:
            removed = next((x for x in items if x.get("id") == remove_id), None)
            if not removed:
                raise LookupError("Attachment not found.")
            items.remove(removed)
        else:
            items.append(attachment)
        case["updated_at"] = now_iso()
        kind = removed if remove_id else attachment
        is_proof = (kind or {}).get("category") == "proof"
        action = (
            ("proof_removed" if remove_id else "proof_added")
            if is_proof
            else ("attachment_removed" if remove_id else "attachment_added")
        )
        case.setdefault("workspace_history", []).append({
            "action": action, "author": actor["name"], "created_at": now_iso(),
        })
        _upsert_case_row(conn, case)
        return removed


def workspace_telegram_media_remove(actor, case_id: str, index: int):
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        case = json.loads(row["data_json"]) if row else None
        if not case or not is_maintenance_report(case):
            raise LookupError("Case not found.")
        if actor["role"] not in ("developer", "super_admin") and str(case.get("agent_id")) != str(actor["id"]):
            raise PermissionError("You cannot manage this case.")
        media = case.get("media") or []
        if not isinstance(media, list) or not 0 <= index < len(media):
            raise LookupError("Telegram attachment not found.")
        removed = media.pop(index)
        case["media"] = media
        case["updated_at"] = now_iso()
        media_type = ""
        if isinstance(removed, (list, tuple)) and removed:
            media_type = str(removed[0])
        elif isinstance(removed, dict):
            media_type = str(removed.get("type", ""))
        case.setdefault("workspace_history", []).append({
            "action": "telegram_attachment_removed",
            "author": actor.get("name", "Team member"),
            "created_at": now_iso(),
            "media_type": media_type[:30],
        })
        _upsert_case_row(conn, case)
        return removed


def workspace_attachment_lookup(case_id, attachment_id):
    case = get_case(case_id)
    if not case or not is_maintenance_report(case):
        raise LookupError("Case not found.")
    item = next((x for x in case.get("workspace_attachments", []) if x.get("id") == attachment_id), None)
    if not item:
        raise LookupError("Attachment not found.")
    return dict(case), dict(item)
