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
from backend.core.test_case_rules import TEST_LANE_STAGE, should_auto_move_to_test_lane
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


def _upsert_case_row(
    conn: sqlite3.Connection,
    case: dict,
    *,
    event_type: str = "updated",
    actor_id: str = "",
    actor_name: str = "",
) -> None:
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
    db.sync_normalized_case_row(conn, case)
    db.append_case_event(
        conn,
        str(case.get("id") or ""),
        event_type,
        actor_id=actor_id,
        actor_name=actor_name,
        summary={
            "status": str(case.get("status") or ""),
            "agent_id": case.get("agent_id"),
            "board_stage": str(case.get("board_stage") or ""),
            "board_rank": float(case.get("board_rank") or 0.0),
            "priority": str(case.get("priority") or "normal"),
            "unit_number": str(case.get("unit_number") or ""),
        },
        created_at=str(case.get("updated_at") or case.get("opened_at") or ""),
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
        "board_stage":     "",
        "status":          "open",
        "notes":           None,
        "report_msg_id":   None,
    }
    if should_auto_move_to_test_lane(case):
        case["board_stage"] = TEST_LANE_STAGE
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
    saved_snapshot = None
    storage_dir = _storage_dir()
    with db.write_tx(storage_dir) as conn:
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
            if not case.get("board_stage") and not case.get("test_lane_overridden") and should_auto_move_to_test_lane(case):
                case["board_stage"] = TEST_LANE_STAGE
        _upsert_case_row(conn, case)
        saved_snapshot = dict(case)
    if saved_snapshot and saved_snapshot.get("status") == "reported" and not saved_snapshot.get("closed_at"):
        from backend.core.test_case_ai import schedule_test_case_review

        schedule_test_case_review(case_id, saved_snapshot, storage_dir=storage_dir)
        refreshed = get_case(case_id)
        if isinstance(refreshed, dict):
            return refreshed
    return saved_snapshot


def add_system_test_note(case_id: str, text: str) -> Optional[dict]:
    """Append an internal System note once for a possible test case without changing its lane."""
    clean_text = " ".join(str(text or "").split()).strip()[:1000]
    if not clean_text:
        return None
    import uuid

    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            return None
        case = json.loads(row["data_json"])
        if case.get("status") == "done" or case.get("closed_at"):
            return None
        if str(case.get("board_stage") or "").strip().lower() == TEST_LANE_STAGE:
            return None
        notes = case.setdefault("workspace_notes", [])
        if any(
            isinstance(n, dict) and not n.get("deleted_at") and n.get("kind") == "test_suspect"
            for n in notes
        ):
            return dict(case)
        now = now_iso()
        notes.append({
            "id": f"sys-test-{uuid.uuid4().hex[:12]}",
            "text": clean_text,
            "author": "System",
            "author_id": "system",
            "author_role": "system",
            "system": True,
            "kind": "test_suspect",
            "created_at": now,
            "reply_to_id": None,
            "file_ids": [],
            "mentions": [],
            "pinned": False,
        })
        case["updated_at"] = now
        _upsert_case_row(
            conn,
            case,
            event_type="note",
            actor_id="system",
            actor_name="System",
        )
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
    closed_by_id=None,
    closed_by_name: Optional[str] = None,
) -> Optional[dict]:
    """Close atomically, retaining explicitly reported replaced part numbers."""
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
        actor_id = closed_by_id if closed_by_id is not None else case.get("agent_id")
        actor_name = (
            str(closed_by_name).strip()
            if closed_by_name
            else str(case.get("agent_name") or "").strip() or "Telegram Bot"
        )
        if not case.get("closed_by_id") and actor_id is not None:
            case["closed_by_id"] = actor_id
        if not case.get("closed_by_name"):
            case["closed_by_name"] = actor_name
        _upsert_case_row(
            conn,
            case,
            event_type="close",
            actor_id=str(actor_id or ""),
            actor_name=actor_name,
        )
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
    storage_dir = _storage_dir()
    should_review = False
    written_case = None
    with db.write_tx(storage_dir) as conn:
        now = now_iso()
        if case_id is None:
            existing_row = conn.execute(
                "SELECT data_json FROM cases WHERE workspace_request_id=? AND created_by_id=?",
                (payload["request_id"], str(actor["id"])),
            ).fetchone()
            if existing_row:
                return json.loads(existing_row["data_json"])
            if payload.get("unassigned"):
                target = {"id": None, "name": "", "username": ""}
            else:
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
                "assigned_at":          now if target["id"] is not None else None,
                "closed_at":            None,
                "agent_id":             target["id"],
                "agent_name":           target["name"],
                "agent_username":       target.get("username", ""),
                "board_stage":          str(payload.get("board_stage") or ""),
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
            if not case["board_stage"] and should_auto_move_to_test_lane(case):
                case["board_stage"] = TEST_LANE_STAGE
                case.setdefault("workspace_history", []).append({
                    "action": "stage",
                    "author": "Auto-Sort",
                    "from": "",
                    "to": TEST_LANE_STAGE,
                    "created_at": now,
                })
            else:
                should_review = True
        else:
            row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                raise LookupError("Case not found.")
            case = json.loads(row["data_json"])
            if payload["action"] not in ("reassign", "stage", "move", "rank", "set_test") and not is_maintenance_report(case):
                raise LookupError("Maintenance report not found. Awaiting Telegram report.")
            action = payload["action"]
            actor_role = str(actor.get("role") or "").lower()
            is_dev = actor_role == "developer"
            case_is_test = bool(case.get("is_test") or case.get("is_testing"))
            if action == "set_test":
                if not is_dev:
                    raise PermissionError("Only developers can mark or unmark test cases.")
            elif case_is_test and not is_dev:
                raise PermissionError("This case is marked as Test and is read-only for non-developers.")
            collaboration = action in ("note", "note_edit", "note_delete", "note_pin")
            owns_unassigned = (not case.get("agent_id")) and str(case.get("created_by_id") or "") == str(actor["id"])
            if action != "set_test" and not collaboration and actor["role"] not in ("developer", "super_admin") and str(case.get("agent_id")) != str(actor["id"]) and not owns_unassigned:
                raise PermissionError("Only the assigned agent or a manager can update this case.")
            if payload["action"] == "close" and case.get("status") == "done":
                return case
            if payload["action"] == "reopen" and case.get("status") != "done" and not case.get("closed_at"):
                return case
            if action != "set_test" and payload.get("revision") != workspace_revision(case):
                # Allow field-level atomic merge when editing non-overlapping fields, or fractional rank updates.
                can_merge = False
                if action == "rank" or payload.get("allow_concurrent_move"):
                    can_merge = True
                elif action == "edit" and isinstance(payload.get("base_fields"), dict):
                    base_fields = payload["base_fields"]
                    fields_to_write = payload.get("fields") or {}
                    can_merge = bool(fields_to_write) and all(
                        str(case.get(k) or "") == str(base_fields.get(k) or "")
                        for k in fields_to_write
                    )
                if not can_merge:
                    raise ValueError("This report changed. Refresh its details before saving again.")
            if action not in ("reopen", "set_test") and (case.get("status") not in ("open", "assigned", "reported") or case.get("closed_at")):
                raise ValueError("This report is already closed.")
            if payload["action"] == "close":
                _close_record(case)
                case.update({"closed_by_id": actor["id"], "closed_by_name": actor["name"]})
                conn.execute("DELETE FROM active_alerts WHERE alert_id=?", (case_id,))
            elif action == "reopen":
                prev_closed_by = str(case.get("closed_by_name") or "").strip()
                case.update({
                    "status": "reported",
                    "reported_at": case.get("reported_at") or now,
                    "closed_at": None,
                    "closed_by_id": None,
                    "closed_by_name": None,
                    "resolution_secs": None,
                    "reopened_at": now,
                    "reopened_by_id": actor["id"],
                    "reopened_by_name": actor["name"],
                })
                case.setdefault("workspace_history", []).append({
                    "action": "reopen",
                    "author": actor["name"],
                    "author_id": str(actor["id"]),
                    "created_at": now,
                    "from": "done",
                    "to": "reported",
                    **({"closed_by": prev_closed_by} if prev_closed_by else {}),
                })
            elif action == "set_test":
                next_test = bool(payload.get("is_test"))
                prev_test = bool(case.get("is_test") or case.get("is_testing"))
                case["is_test"] = next_test
                case.pop("is_testing", None)
                if next_test:
                    case["test_marked_by_id"] = str(actor.get("id") or "")
                    case["test_marked_by_name"] = actor.get("name") or "Developer"
                    case["test_marked_at"] = now
                else:
                    case["test_marked_by_id"] = ""
                    case["test_marked_by_name"] = ""
                    case["test_marked_at"] = None
                if next_test != prev_test:
                    case.setdefault("workspace_history", []).append({
                        "action": "test_marked" if next_test else "test_unmarked",
                        "author": actor["name"],
                        "author_id": str(actor["id"]),
                        "created_at": now,
                    })
            elif action == "rank":
                case["board_rank"] = float(payload.get("board_rank") or 0.0)
            elif action == "note":
                import uuid
                text = payload.get("text", "")
                file_ids = payload.get("file_ids", [])
                existing_files = {str(f.get("id")) for f in case.get("workspace_attachments", []) if isinstance(f, dict)}
                if not set(file_ids).issubset(existing_files):
                    raise ValueError("An attachment is no longer available. Refresh the case.")
                notes = case.setdefault("workspace_notes", [])
                reply = payload.get("reply_to_id", "")
                if reply and not any(n.get("id", "legacy-" + str(i)) == reply and not n.get("deleted_at") for i,n in enumerate(notes) if isinstance(n, dict)):
                    raise ValueError("The replied-to comment is no longer available.")
                note_id = uuid.uuid4().hex
                # Resolve @usernames or @names against the active roster in the same transaction.
                import re
                found = {x.casefold() for x in re.findall(r"(?<!\w)@([\w.-]{2,40})", text)}
                users = conn.execute("SELECT id,name,username FROM users").fetchall() if found else []
                mentions = []
                for u in users:
                    uname = str(u["username"] or "").lstrip("@").strip().casefold()
                    name_raw = str(u["name"] or "").strip()
                    name_fold = name_raw.casefold()
                    name_slug = re.sub(r"\s+", "_", name_raw).casefold()
                    first_token = name_raw.split()[0].casefold() if name_raw else ""
                    if any(tok and tok in found for tok in (uname, name_fold, name_slug, first_token)):
                        mentions.append({"id": str(u["id"]), "name": u["name"]})
                notes.append({"id": note_id, "text": text, "author": actor["name"],
                              "author_id": str(actor["id"]), "author_role": actor.get("role", "agent"),
                              "created_at": now,
                              "reply_to_id": reply or None, "file_ids": list(file_ids),
                              "mentions": mentions, "pinned": False})
                for mentioned in mentions:
                    if mentioned["id"] == str(actor["id"]):
                        continue
                    nid = "mention-" + uuid.uuid4().hex
                    notification = {"id": nid, "user": mentioned["id"], "kind": "mention",
                                    "title": actor["name"] + " mentioned you",
                                    "message": "In a Live Board case for unit " + str(case.get("unit_number") or "Unknown")[:70],
                                    "case_id": case_id, "seen": False, "created_at": now}
                    conn.execute("INSERT INTO notifications(id,user_key,created_at,seen,data_json) VALUES(?,?,?,?,?)",
                                 (nid, mentioned["id"], now, 0, json.dumps(notification, ensure_ascii=False)))
                history_note = {
                    "action": "note",
                    "author": actor["name"],
                    "author_id": str(actor["id"]),
                    "note_id": note_id,
                    "created_at": now,
                    "preview": (text or "").strip()[:140],
                }
                if file_ids:
                    history_note["file_count"] = len(file_ids)
                case.setdefault("workspace_history", []).append(history_note)
            elif action in ("note_edit", "note_delete", "note_pin"):
                notes = case.setdefault("workspace_notes", [])
                note_id = payload.get("note_id", "")
                index = payload.get("index", -1)
                if note_id:
                    index = next((i for i,n in enumerate(notes) if isinstance(n, dict) and
                                  n.get("id", "legacy-" + str(i)) == note_id), -1)
                if not isinstance(index, int) or not 0 <= index < len(notes) or not isinstance(notes[index], dict):
                    raise ValueError("Comment no longer exists. Refresh the case.")
                note = notes[index]
                if note.get("deleted_at"):
                    raise ValueError("This comment was already deleted.")
                manager = actor["role"] in ("developer", "super_admin")
                own = bool(note.get("author_id")) and str(note["author_id"]) == str(actor["id"])
                if action in ("note_edit", "note_delete") and not (own or manager):
                    raise PermissionError("You can edit or delete only your own comments.")
                if action == "note_pin" and not (manager or str(case.get("agent_id")) == str(actor["id"])):
                    raise PermissionError("Only the assigned agent or a manager can pin comments.")
                note_preview = str(note.get("text") or "").strip()[:140]
                if action == "note_edit":
                    if not payload.get("text", "").strip() and not note.get("file_ids"):
                        raise ValueError("A comment must contain text or an attachment.")
                    note["text"] = payload["text"]
                    note["edited_at"] = now
                    note_preview = str(payload["text"] or "").strip()[:140]
                elif action == "note_delete":
                    note["text"] = ""
                    note["file_ids"] = []
                    note["deleted_at"] = now
                    note["deleted_by"] = actor["name"]
                    note["pinned"] = False
                else:
                    note["pinned"] = bool(payload["pinned"])
                case.setdefault("workspace_history", []).append({
                    "action": action,
                    "author": actor["name"],
                    "author_id": str(actor["id"]),
                    "note_id": note.get("id", "legacy-" + str(index)),
                    "created_at": now,
                    "preview": note_preview,
                    **({"pinned": bool(note["pinned"])} if action == "note_pin" else {}),
                })
            elif payload["action"] in ("reassign", "move"):
                target = payload["target"]
                has_rank = payload.get("board_rank") is not None
                if str(case.get("agent_id")) == str(target["id"]) and (
                    payload["action"] != "move" or (case.get("board_stage") or "") == payload["stage"]
                ) and not has_rank:
                    return dict(case)
                if has_rank:
                    case["board_rank"] = float(payload["board_rank"])
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
                    if old_stage == TEST_LANE_STAGE and payload["stage"] != TEST_LANE_STAGE:
                        case["test_lane_overridden"] = True
                    elif payload["stage"] == TEST_LANE_STAGE:
                        case.pop("test_lane_overridden", None)
                    case.setdefault("workspace_history", []).append({
                        "action": "stage", "author": actor["name"],
                        "from": old_stage, "to": payload["stage"], "created_at": now,
                    })
            elif payload["action"] == "stage":
                previous = case.get("board_stage") or ""
                if payload.get("board_rank") is not None:
                    case["board_rank"] = float(payload["board_rank"])
                case["board_stage"] = payload["stage"]
                if previous == TEST_LANE_STAGE and payload["stage"] != TEST_LANE_STAGE:
                    case["test_lane_overridden"] = True
                elif payload["stage"] == TEST_LANE_STAGE:
                    case.pop("test_lane_overridden", None)
                if previous != payload["stage"]:
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
                edit_changes = []
                for key, new_val in fields.items():
                    old_raw = case.get(key)
                    if old_raw is None and key == "load_type" and isinstance(case.get("report_data"), dict):
                        old_raw = case["report_data"].get("load")
                    old_str = str(old_raw or "").strip()
                    new_str = str(new_val or "").strip()
                    if old_str != new_str:
                        edit_changes.append({"field": key, "from": old_str[:140], "to": new_str[:140]})
                case.update(fields)
                if isinstance(case.get("report_data"), dict):
                    for key, value in fields.items():
                        report_key = {"report_driver": "driver", "load_type": "load", "group_name": "group"}.get(key, key)
                        if report_key in case["report_data"]:
                            case["report_data"][report_key] = value
                if (
                    not (case.get("board_stage") or "")
                    and not case.get("test_lane_overridden")
                    and should_auto_move_to_test_lane(case)
                ):
                    case["board_stage"] = TEST_LANE_STAGE
                    case.setdefault("workspace_history", []).append({
                        "action": "stage",
                        "author": "Auto-Sort",
                        "from": "",
                        "to": TEST_LANE_STAGE,
                        "created_at": now,
                    })
                elif (
                    not (case.get("board_stage") or "")
                    and not case.get("test_lane_overridden")
                ):
                    should_review = True
            case["updated_at"] = now
            if action not in ("reassign", "stage", "move", "rank", "reopen", "note", "note_edit", "note_delete", "note_pin", "set_test"):
                history_entry = {
                    "action": action, "author": actor["name"], "created_at": now,
                    "fields": sorted(payload.get("fields", {})) if action == "edit" else [],
                }
                if action == "edit" and edit_changes:
                    history_entry["changes"] = edit_changes
                case.setdefault("workspace_history", []).append(history_entry)
        evt = "created" if case_id is None else str(payload.get("action") or "updated")
        _upsert_case_row(
            conn,
            case,
            event_type=evt,
            actor_id=str(actor.get("id") or ""),
            actor_name=str(actor.get("name") or ""),
        )
        written_case = dict(case)
    if should_review and written_case and written_case.get("id"):
        from backend.core.test_case_ai import schedule_test_case_review

        schedule_test_case_review(str(written_case["id"]), written_case, storage_dir=storage_dir)
        refreshed = get_case(str(written_case["id"]))
        if isinstance(refreshed, dict):
            return refreshed
    return written_case


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
    """Indexed lookup of active unassigned-report cases for Telegram bot (reported cases live only on Live Board)."""
    if status_filter == "reported":
        return []
    sql = "SELECT data_json FROM cases WHERE agent_id=? AND status='assigned' ORDER BY opened_at DESC"
    params: list = [int(agent_id)]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([int(limit), max(0, int(offset))])
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [json.loads(r["data_json"]) for r in rows]


def count_active_cases_for_agent(agent_id: int) -> dict[str, int]:
    """Return Telegram bot active case counts (reported cases live only on Live Board)."""
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM cases WHERE agent_id=? AND status='assigned'",
            (int(agent_id),),
        ).fetchone()
    assigned_cnt = int(row["cnt"]) if row else 0
    return {"assigned": assigned_cnt, "reported": 0, "total": assigned_cnt}


def get_agent_cases_page_data(
    agent_id: int,
    status_filter: Optional[str] = None,
    page: int = 0,
    per_page: int = 5,
) -> tuple[dict[str, int], str, int, int, list[dict]]:
    """Fetch counts + paginated Telegram bot active cases (status='assigned' only)."""
    flt = "assigned" if status_filter == "assigned" else "all"
    per_page = max(1, int(per_page))
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM cases WHERE agent_id=? AND status='assigned'",
            (int(agent_id),),
        ).fetchone()
        assigned_cnt = int(row["cnt"]) if row else 0
        counts = {"assigned": assigned_cnt, "reported": 0, "total": assigned_cnt}
        if counts["total"] == 0:
            return counts, flt, 1, 0, []

        total_pages = max(1, ((assigned_cnt - 1) // per_page) + 1)
        clamped_page = max(0, min(int(page), total_pages - 1))
        offset = clamped_page * per_page

        batch_rows = conn.execute(
            "SELECT data_json FROM cases WHERE agent_id=? AND status='assigned' ORDER BY opened_at DESC LIMIT ? OFFSET ?",
            [int(agent_id), per_page, offset],
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
    """Return latest un-reported active case for an agent in Telegram bot."""
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute(
            "SELECT data_json FROM cases WHERE agent_id=? AND status='assigned' ORDER BY rowid DESC LIMIT 1",
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
        if (
            is_maintenance_report(c)
            and not c.get("board_hidden")
            and (chicago_date_str(c.get("reported_at")) or "") >= min_reported_date
        ):
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

async def async_close_case(
    case_id,
    notes=None,
    *,
    replaced_parts=None,
    parts_replaced=None,
    closed_by_id=None,
    closed_by_name=None,
):
    return await asyncio.to_thread(
        close_case,
        case_id,
        notes,
        replaced_parts=replaced_parts,
        parts_replaced=parts_replaced,
        closed_by_id=closed_by_id,
        closed_by_name=closed_by_name,
    )


def _is_active_live_board_case(case: dict) -> bool:
    return (
        isinstance(case, dict)
        and is_maintenance_report(case)
        and not case.get("board_hidden")
        and case.get("status") == "reported"
        and not case.get("closed_at")
        and (chicago_date_str(case.get("reported_at")) or "") >= "2026-10-12"
    )


def clear_live_board_cases(actor: dict, *, scope: str = "active") -> dict:
    """Remove cards from Live Board while keeping every case record safely in the database."""
    role = str((actor or {}).get("role") or "").lower()
    if role not in ("developer", "super_admin"):
        raise PermissionError("Only managers and developers can clear the Live Board.")
    clean_scope = str(scope or "active").strip().lower()
    if clean_scope not in ("active", "active_and_done", "test_only"):
        raise ValueError("Choose a valid Live Board clear scope.")

    from backend.storage.board_order import KEY as BOARD_ORDER_KEY

    sdir = _storage_dir()
    deleted_ids: list[str] = []
    now = now_iso()
    actor_id = str(actor.get("id") or "")
    actor_name = str(actor.get("name") or "Manager").strip() or "Manager"

    with db.write_tx(sdir) as conn:
        rows = conn.execute(
            "SELECT id, status, closed_at, data_json FROM cases WHERE status IN ('reported', 'done')"
        ).fetchall()
        for row in rows:
            try:
                c = json.loads(row["data_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(c, dict) or c.get("board_hidden"):
                continue
            cid = str(c.get("id") or row["id"] or "").strip()
            if not cid or not is_maintenance_report(c):
                continue
            is_active = _is_active_live_board_case(c)
            is_ws_done = c.get("status") == "done" and any(
                isinstance(h, dict) and h.get("action") == "close"
                for h in (c.get("workspace_history") or [])
            )
            is_test_card = (
                str(c.get("board_stage") or "").strip().lower() == TEST_LANE_STAGE
                or bool(c.get("is_test") or c.get("is_testing"))
            )
            if clean_scope == "active" and not is_active:
                continue
            if clean_scope == "test_only" and not (is_active and is_test_card):
                continue
            if clean_scope == "active_and_done" and not (is_active or is_ws_done):
                continue

            if c.get("status") != "done":
                _close_record(c)
                c.setdefault("closed_by_id", actor.get("id"))
                c.setdefault("closed_by_name", actor_name)
            c["board_hidden"] = True
            c["board_hidden_at"] = now
            c["board_hidden_by"] = actor_name
            c["updated_at"] = now
            c.setdefault("workspace_history", []).append({
                "action": "board_cleared",
                "author": actor_name,
                "author_id": actor_id,
                "scope": clean_scope,
                "created_at": now,
            })
            _upsert_case_row(
                conn,
                c,
                event_type="board_cleared",
                actor_id=actor_id,
                actor_name=actor_name,
            )
            deleted_ids.append(cid)

        if deleted_ids:
            for idx in range(0, len(deleted_ids), 250):
                batch = deleted_ids[idx:idx + 250]
                placeholders = ",".join("?" for _ in batch)
                conn.execute(f"DELETE FROM active_alerts WHERE alert_id IN ({placeholders})", batch)

            order_row = conn.execute("SELECT version, data_json FROM kv_state WHERE key=?", (BOARD_ORDER_KEY,)).fetchone()
            if order_row:
                try:
                    groups = json.loads(order_row["data_json"])
                except (TypeError, ValueError):
                    groups = {}
                if isinstance(groups, dict):
                    deleted_set = set(deleted_ids)
                    cleaned_groups = {
                        k: [x for x in v if x not in deleted_set]
                        for k, v in groups.items()
                        if isinstance(v, list)
                    }
                    conn.execute(
                        "UPDATE kv_state SET version=?, data_json=? WHERE key=?",
                        (int(order_row["version"]) + 1, json.dumps(cleaned_groups, separators=(",", ":")), BOARD_ORDER_KEY),
                    )
            db.bump_version(conn, "cases_version")

    logger.warning(
        "Live Board cards archived from board by %s (%s): scope=%s count=%d (cases preserved in SQLite)",
        actor_name,
        actor_id,
        clean_scope,
        len(deleted_ids),
    )
    return {
        "deleted_ids": deleted_ids,
        "deleted_count": len(deleted_ids),
        "preserved_in_db": True,
        "scope": clean_scope,
    }


def close_all_live_board_cases(actor: dict, *, scope: str = "active") -> dict:
    """Close all active Live Board cards in a single transaction (Managers/Developers only)."""
    role = str((actor or {}).get("role") or "").lower()
    if role not in ("developer", "super_admin"):
        raise PermissionError("Only managers and developers can bulk-close the Live Board.")
    clean_scope = str(scope or "active").strip().lower()
    if clean_scope not in ("active", "test_only"):
        raise ValueError("Choose a valid close-all scope.")

    sdir = _storage_dir()
    is_dev = role == "developer"
    actor_id = str(actor.get("id") or "")
    actor_name = str(actor.get("name") or "Manager").strip() or "Manager"
    now = now_iso()
    closed_ids: list[str] = []
    skipped_count = 0

    with db.write_tx(sdir) as conn:
        rows = conn.execute(
            "SELECT id, data_json FROM cases WHERE status='reported' AND (closed_at IS NULL OR closed_at='')"
        ).fetchall()
        for row in rows:
            try:
                c = json.loads(row["data_json"])
            except (TypeError, ValueError):
                continue
            if not _is_active_live_board_case(c):
                continue
            is_test_card = (
                str(c.get("board_stage") or "").strip().lower() == TEST_LANE_STAGE
                or bool(c.get("is_test") or c.get("is_testing"))
            )
            if clean_scope == "test_only" and not is_test_card:
                continue
            if bool(c.get("is_test") or c.get("is_testing")) and not is_dev:
                skipped_count += 1
                continue
            cid = str(c.get("id") or row["id"] or "").strip()
            if not cid:
                continue
            _close_record(c)
            c["closed_by_id"] = actor.get("id")
            c["closed_by_name"] = actor_name
            c.setdefault("workspace_history", []).append({
                "action": "close",
                "author": actor_name,
                "author_id": actor_id,
                "created_at": now,
                "fields": [],
            })
            _upsert_case_row(
                conn,
                c,
                event_type="close",
                actor_id=actor_id,
                actor_name=actor_name,
            )
            conn.execute("DELETE FROM active_alerts WHERE alert_id=?", (cid,))
            closed_ids.append(cid)

    return {
        "closed_ids": closed_ids,
        "closed_count": len(closed_ids),
        "skipped_count": skipped_count,
        "scope": clean_scope,
    }


def sync_user_display_on_cases(user_id: int, name: str, username: str) -> int:
    """Propagate a user's updated display name/@username across their active Live Board cases."""
    sdir = _storage_dir()
    clean_name = str(name or "").strip()
    clean_uname = str(username or "").strip().lstrip("@")
    updated = 0
    now = now_iso()
    with db.write_tx(sdir) as conn:
        rows = conn.execute(
            "SELECT id, data_json FROM cases WHERE agent_id=? AND status IN ('open','assigned','reported')",
            (int(user_id),),
        ).fetchall()
        for r in rows:
            try:
                c = json.loads(r["data_json"])
            except Exception:
                continue
            if not isinstance(c, dict):
                continue
            if c.get("agent_name") == clean_name and c.get("agent_username", "") == clean_uname:
                continue
            c["agent_name"] = clean_name
            c["agent_username"] = clean_uname
            c["updated_at"] = now
            _upsert_case_row(conn, c, event_type="updated", actor_id=str(user_id), actor_name=clean_name)
            updated += 1
    return updated


def bulk_update_user_cases(actor: dict, target_user_id: int, payload: dict) -> dict:
    """Batch-edit a specific user's Live Board cases (Managers/Developers only)."""
    role = str((actor or {}).get("role") or "").lower()
    if role not in ("developer", "super_admin"):
        raise PermissionError("Only managers and developers can edit user case batches.")
    if not isinstance(payload, dict):
        raise ValueError("Invalid update payload.")

    from backend.storage.board_order import allowed_stages
    from backend.storage.user_store import get_user

    sdir = _storage_dir()
    is_dev = role == "developer"
    actor_id = str(actor.get("id") or "")
    actor_name = str(actor.get("name") or "Manager").strip() or "Manager"
    now = now_iso()

    case_ids_filter = payload.get("case_ids")
    selected_set = (
        {str(x).strip() for x in case_ids_filter if str(x or "").strip()}
        if isinstance(case_ids_filter, list) and case_ids_filter
        else None
    )

    bulk_action = str(payload.get("bulk_action") or "").strip().lower()
    if bulk_action not in ("", "close", "reopen", "clear_from_board"):
        raise ValueError("Choose a valid bulk action.")

    has_reassign = "reassign_to_id" in payload and str(payload.get("reassign_to_id") or "").strip() != ""
    target_assignee = None
    unassign = False
    if has_reassign:
        raw_target = str(payload.get("reassign_to_id") or "").strip()
        if raw_target == "unassigned":
            unassign = True
        else:
            if not raw_target.isdecimal():
                raise ValueError("Choose a valid teammate to reassign cases to.")
            target_assignee = get_user(int(raw_target))
            if not target_assignee:
                raise ValueError("Target agent account not found.")

    has_stage = "board_stage" in payload and payload.get("board_stage") is not None
    stage_val = None
    if has_stage:
        stage_val = str(payload.get("board_stage") or "").strip()
        if stage_val not in allowed_stages(sdir):
            raise ValueError("Choose a valid workflow stage.")

    has_prio = "priority" in payload and str(payload.get("priority") or "").strip() != ""
    prio_val = None
    if has_prio:
        prio_val = str(payload.get("priority") or "").strip().lower()
        if prio_val not in ("low", "medium", "high", "normal"):
            raise ValueError("Choose a valid priority.")

    has_shop = "shop_info" in payload and payload.get("shop_info") is not None
    shop_val = str(payload.get("shop_info") or "").strip()[:320] if has_shop else None

    if not any([bulk_action, has_reassign, has_stage, has_prio, has_shop]):
        raise ValueError("Choose at least one field or action to apply.")

    updated_ids: list[str] = []
    skipped_count = 0

    with db.write_tx(sdir) as conn:
        rows = conn.execute(
            "SELECT id, data_json FROM cases WHERE agent_id=? AND status IN ('reported', 'done')",
            (int(target_user_id),),
        ).fetchall()
        for row in rows:
            try:
                c = json.loads(row["data_json"])
            except Exception:
                continue
            if not isinstance(c, dict) or not is_maintenance_report(c):
                continue
            cid = str(c.get("id") or row["id"] or "").strip()
            if selected_set is not None and cid not in selected_set:
                continue
            is_active = _is_active_live_board_case(c)
            is_ws_done = c.get("status") == "done" and not c.get("board_hidden") and any(
                isinstance(h, dict) and h.get("action") == "close"
                for h in (c.get("workspace_history") or [])
            )
            if bulk_action == "reopen":
                if c.get("status") != "done" and not c.get("board_hidden"):
                    continue
            elif not is_active and not (bulk_action == "clear_from_board" and is_ws_done):
                continue

            if bool(c.get("is_test") or c.get("is_testing")) and not is_dev:
                skipped_count += 1
                continue

            if bulk_action == "reopen":
                c.update({
                    "status": "reported",
                    "reported_at": c.get("reported_at") or now,
                    "closed_at": None,
                    "closed_by_id": None,
                    "closed_by_name": None,
                    "resolution_secs": None,
                    "board_hidden": False,
                    "reopened_at": now,
                    "reopened_by_id": actor.get("id"),
                    "reopened_by_name": actor_name,
                })
                c.pop("board_hidden_at", None)
                c.pop("board_hidden_by", None)

            if unassign:
                c["agent_id"] = None
                c["agent_name"] = "Unassigned"
                c["agent_username"] = ""
            elif target_assignee is not None:
                c["agent_id"] = int(payload["reassign_to_id"])
                c["agent_name"] = str(target_assignee.get("name") or "")
                c["agent_username"] = str(target_assignee.get("username") or "").lstrip("@")

            if has_stage:
                c["board_stage"] = stage_val
            if has_prio:
                c["priority"] = prio_val
            if has_shop:
                c["shop_info"] = shop_val

            if bulk_action == "close" and c.get("status") != "done":
                _close_record(c)
                c["closed_by_id"] = actor.get("id")
                c["closed_by_name"] = actor_name
                c.setdefault("workspace_history", []).append({
                    "action": "close",
                    "author": actor_name,
                    "author_id": actor_id,
                    "created_at": now,
                    "fields": [],
                })
                conn.execute("DELETE FROM active_alerts WHERE alert_id=?", (cid,))
            elif bulk_action == "clear_from_board":
                if c.get("status") != "done":
                    _close_record(c)
                    c.setdefault("closed_by_id", actor.get("id"))
                    c.setdefault("closed_by_name", actor_name)
                c["board_hidden"] = True
                c["board_hidden_at"] = now
                c["board_hidden_by"] = actor_name
                conn.execute("DELETE FROM active_alerts WHERE alert_id=?", (cid,))

            c["updated_at"] = now
            c.setdefault("workspace_history", []).append({
                "action": "user_bulk_edit",
                "author": actor_name,
                "author_id": actor_id,
                "created_at": now,
                "bulk_action": bulk_action or "edit",
            })
            _upsert_case_row(conn, c, event_type=bulk_action or "updated", actor_id=actor_id, actor_name=actor_name)
            updated_ids.append(cid)

    return {"updated_ids": updated_ids, "updated_count": len(updated_ids), "skipped_count": skipped_count}

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

def _sanitize_attachment_filename(raw_name: str, fallback_ext: str = "") -> str:
    import re
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(raw_name or "").strip()).strip(" .")[:140]
    if not clean:
        raise ValueError("Provide a valid file name.")
    ext = str(fallback_ext or "").lower()
    if ext and not clean.lower().endswith(ext):
        clean = f"{clean}{ext}"
    return clean


def _can_edit_attachment(actor: dict, case: dict, target: dict) -> bool:
    return (
        actor.get("role") in ("developer", "super_admin")
        or str(target.get("uploaded_by_id") or "") == str(actor.get("id") or "")
        or str(case.get("agent_id") or "") == str(actor.get("id") or "")
    )


def workspace_attachment_write(
    actor,
    case_id,
    attachment=None,
    remove_id=None,
    rename_id=None,
    new_name=None,
    update_id=None,
    updates=None,
    new_version_id=None,
    version_meta=None,
):
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        case = json.loads(row["data_json"]) if row else None
        if not case or not is_maintenance_report(case):
            raise LookupError("Case not found.")
        if bool(case.get("is_test") or case.get("is_testing")) and str(actor.get("role") or "").lower() != "developer":
            raise PermissionError("This case is marked as Test and is read-only for non-developers.")
        items = case.setdefault("workspace_attachments", [])
        target_id = update_id or rename_id or new_version_id
        if target_id:
            target = next((x for x in items if x.get("id") == target_id), None)
            if not target:
                raise LookupError("Attachment not found.")
            if not _can_edit_attachment(actor, case, target):
                raise PermissionError("Only the assigned agent, uploader, or a manager can update this attachment.")
            if new_version_id:
                if not isinstance(version_meta, dict) or not version_meta.get("blob_id"):
                    raise ValueError("Missing uploaded file version metadata.")
                versions = target.setdefault("versions", [])
                if not versions:
                    versions.append({
                        "id": str(target.get("blob_id") or target["id"]),
                        "blob_id": str(target.get("blob_id") or target["id"]),
                        "version": 1,
                        "name": str(target.get("name") or "File"),
                        "size": int(target.get("size") or 0),
                        "extension": str(target.get("extension") or ""),
                        "created_at": str(target.get("created_at") or ""),
                        "uploaded_by": str(target.get("uploaded_by") or "Team member"),
                        "uploaded_by_id": str(target.get("uploaded_by_id") or ""),
                    })
                next_ver = max((int(v.get("version") or 0) for v in versions), default=1) + 1
                new_ver_entry = {
                    "id": str(version_meta["blob_id"]),
                    "blob_id": str(version_meta["blob_id"]),
                    "version": next_ver,
                    "name": str(version_meta.get("name") or target.get("name") or "File"),
                    "size": int(version_meta.get("size") or 0),
                    "extension": str(version_meta.get("extension") or target.get("extension") or ""),
                    "created_at": str(version_meta.get("created_at") or now_iso()),
                    "uploaded_by": str(version_meta.get("uploaded_by") or actor.get("name") or "Team member"),
                    "uploaded_by_id": str(version_meta.get("uploaded_by_id") or actor.get("id") or ""),
                }
                versions.append(new_ver_entry)
                target["version_num"] = next_ver
                target["blob_id"] = new_ver_entry["blob_id"]
                target["name"] = new_ver_entry["name"]
                target["size"] = new_ver_entry["size"]
                target["extension"] = new_ver_entry["extension"]
                target["created_at"] = new_ver_entry["created_at"]
                target["uploaded_by"] = new_ver_entry["uploaded_by"]
                target["uploaded_by_id"] = new_ver_entry["uploaded_by_id"]
                case["updated_at"] = now_iso()
                case.setdefault("workspace_history", []).append({
                    "action": "attachment_version_added",
                    "author": actor.get("name") or "Team member",
                    "file_name": target["name"][:140],
                    "version": next_ver,
                    "created_at": now_iso(),
                })
                _upsert_case_row(conn, case)
                return dict(target)

            patch = dict(updates) if isinstance(updates, dict) else {}
            if rename_id and new_name is not None:
                patch.setdefault("name", new_name)
            changed_actions = []
            if "name" in patch and patch["name"] is not None:
                old_name = target.get("name", "")
                clean_name = _sanitize_attachment_filename(patch["name"], target.get("extension") or "")
                if clean_name != old_name:
                    target["name"] = clean_name
                    changed_actions.append({
                        "action": "attachment_renamed",
                        "author": actor.get("name") or "Team member",
                        "from": old_name,
                        "to": clean_name,
                        "created_at": now_iso(),
                    })
            if "description" in patch:
                target["description"] = str(patch.get("description") or "").strip()[:500]
                changed_actions.append({
                    "action": "attachment_updated",
                    "author": actor.get("name") or "Team member",
                    "file_name": str(target.get("name") or "")[:140],
                    "created_at": now_iso(),
                })
            if "category" in patch:
                next_cat = str(patch.get("category") or "attachment").strip().lower()
                if next_cat not in ("attachment", "proof"):
                    raise ValueError("Invalid document category.")
                prev_cat = str(target.get("category") or "attachment").lower()
                target["category"] = next_cat
                if next_cat == "proof":
                    pt = str(patch.get("proof_type") or target.get("proof_type") or "repair_proof").strip().lower()
                    if pt not in ("invoice", "repair_proof", "receipt", "other"):
                        raise ValueError("Invalid proof type.")
                    target["proof_type"] = pt
                    if prev_cat != "proof":
                        changed_actions.append({
                            "action": "proof_added",
                            "author": actor.get("name") or "Team member",
                            "file_name": str(target.get("name") or "")[:140],
                            "category": "proof",
                            "created_at": now_iso(),
                        })
                else:
                    target["proof_type"] = ""
            elif "proof_type" in patch and str(target.get("category") or "").lower() == "proof":
                pt = str(patch.get("proof_type") or "repair_proof").strip().lower()
                if pt not in ("invoice", "repair_proof", "receipt", "other"):
                    raise ValueError("Invalid proof type.")
                target["proof_type"] = pt

            if changed_actions:
                case["updated_at"] = now_iso()
                case.setdefault("workspace_history", []).extend(changed_actions)
            _upsert_case_row(conn, case)
            return dict(target)
        if remove_id:
            existing = next((x for x in items if x.get("id") == remove_id), None)
            if existing and actor["role"] not in ("developer", "super_admin") and str(existing.get("uploaded_by_id")) != str(actor["id"]):
                raise PermissionError("You can remove only your own attachments.")
        if not remove_id and (case.get("status") not in ("open", "assigned", "reported") or case.get("closed_at")):
            raise ValueError("Cannot upload to a closed case.")
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
            "action": action,
            "author": actor["name"],
            "created_at": now_iso(),
            "file_name": str((kind or {}).get("name") or "")[:140],
            "category": str((kind or {}).get("category") or "")[:32],
        })
        _upsert_case_row(conn, case)
        return removed


def workspace_telegram_media_remove(actor, case_id: str, index: int):
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT data_json FROM cases WHERE id=?", (case_id,)).fetchone()
        case = json.loads(row["data_json"]) if row else None
        if not case or not is_maintenance_report(case):
            raise LookupError("Case not found.")
        if bool(case.get("is_test") or case.get("is_testing")) and str(actor.get("role") or "").lower() != "developer":
            raise PermissionError("This case is marked as Test and is read-only for non-developers.")
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
