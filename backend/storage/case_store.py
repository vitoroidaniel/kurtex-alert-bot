"""
storage/case_store.py — Volume-backed JSON storage.

All data lives in /app/data/ which must be a Railway Volume mount.
Files survive restarts and redeploys permanently.

  /app/data/cases.json         — all case records
  /app/data/active_alerts.json — in-flight alerts (rebuilt on startup)
"""

import copy
import asyncio
import json
import logging
import os
import threading
import fcntl
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.core.app_time import chicago_date_str, parse_timestamp, today_str, utc_now, week_start_str

logger = logging.getLogger(__name__)

DATA_DIR     = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CASES_FILE   = DATA_DIR / "cases.json"
ALERTS_FILE  = DATA_DIR / "active_alerts.json"
class _ProcessFileLock:
    """Reentrant across nested helpers and exclusive across API/bot processes.

    An atomic os.replace alone prevents partial files, not lost updates when
    multiple processes load/modify/save the same JSON document concurrently.
    All existing case-store read/modify/write blocks already use _FILE_LOCK.
    """
    def __init__(self, path):
        self.path = path
        self.thread_lock = threading.RLock()
        self.local = threading.local()

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            depth = getattr(self.local, 'depth', 0)
            if depth == 0:
                fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except BaseException:
                    os.close(fd)
                    raise
                self.local.fd = fd
            self.local.depth = depth + 1
            return self
        except BaseException:
            self.thread_lock.release()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            depth = self.local.depth - 1
            self.local.depth = depth
            if depth == 0:
                fd = self.local.fd
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
                    del self.local.fd
        finally:
            self.thread_lock.release()

_FILE_LOCK = _ProcessFileLock(DATA_DIR / '.cases-write.lock')
_READ_CACHE = {}


# ── Atomic write helpers ──────────────────────────────────────────────────────

def _load(path: Path, *, strict: bool = False) -> list[dict]:
    with _FILE_LOCK:
        if not path.exists():
            if path == CASES_FILE and path.with_name('.cases-initialized').exists():
                raise FileNotFoundError('Previously initialized case storage is missing; refusing to reset cases.')
            return []
        try:
            stat=path.stat()
            signature=(stat.st_ino,stat.st_size,stat.st_mtime_ns)
            cached=_READ_CACHE.get(path)
            if cached is None or cached[0]!=signature:
                value=json.loads(path.read_text(encoding="utf-8"))
                flat=isinstance(value,list) and all(isinstance(row,dict) and all(not isinstance(v,(list,dict)) for v in row.values()) for row in value)
                _READ_CACHE[path]=(signature,value,flat)
            cached=_READ_CACHE[path]
            return [dict(row) for row in cached[1]] if cached[2] else copy.deepcopy(cached[1])
        except Exception as e:
            logger.error(f"Failed to load {path.name}: {e}")
            if strict or path == CASES_FILE:
                raise
            return []


def _save(path: Path, data: list[dict] | dict, *, strict: bool = False) -> None:
    """Durable atomic write protected against overlapping handler callbacks."""
    with _FILE_LOCK:
        tmp = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            payload = json.dumps(data, separators=(",", ":"), default=str)
            with tmp.open("w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            if path == CASES_FILE:
                marker = path.with_name('.cases-initialized')
                if not marker.exists():
                    marker.touch(mode=0o600)
            _READ_CACHE.pop(path,None)
        except Exception as e:
            logger.error(f"Failed to save {path.name}: {e}")
            if strict or path == CASES_FILE:
                raise
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def now_iso() -> str:
    return utc_now().isoformat()


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
    with _FILE_LOCK:
        cases = _load(CASES_FILE)
        cases.append(case)
        _save(CASES_FILE, cases)
    logger.info(f"Case {case_id} created")
    return case


def assign_case(
    case_id: str,
    agent_id: int,
    agent_name: str,
    agent_username: Optional[str],
    allow_reassign: bool = False,
) -> Optional[dict]:
    """Atomically claim an unassigned case, or explicitly reassign it.

    Returning ``None`` means another agent already owns the case. Keeping the
    check and write under one lock prevents two nearly simultaneous button
    clicks from both succeeding.
    """
    with _FILE_LOCK:
        cases = _load(CASES_FILE)
        for case in cases:
            if case["id"] != case_id:
                continue
            if case.get("status") == "done":
                return None
            previous_agent_id = case.get("agent_id")
            if previous_agent_id is not None and not allow_reassign:
                return None
            assigned_at   = now_iso()
            opened_at_dt  = parse_timestamp(case.get("opened_at"))
            assigned_at_dt = parse_timestamp(assigned_at)
            response_secs = int(
                (assigned_at_dt - opened_at_dt).total_seconds()
            ) if opened_at_dt and assigned_at_dt else None
            case.update({
                "assigned_at":    assigned_at,
                "agent_id":       agent_id,
                "agent_name":     agent_name,
                "agent_username": agent_username,
                "status":         "assigned",
                "response_secs":  response_secs,
                "reassigned":     bool(previous_agent_id is not None),
            })
            _save(CASES_FILE, cases)
            logger.info(f"Case {case_id} assigned to {agent_name}")
            return dict(case)
    logger.warning(f"assign_case: {case_id} not found")
    return None


def report_case(case_id: str, notes: Optional[str] = "case reported") -> Optional[dict]:
    with _FILE_LOCK:
        cases = _load(CASES_FILE)
        for case in cases:
            if case["id"] == case_id:
                if case.get("status") == "done" or case.get("closed_at"):
                    return case
                case.update({"status": "reported", "notes": notes})
                case["reported_at"] = case["updated_at"] = now_iso()
                _save(CASES_FILE, cases)
                return case
    return None


def save_maintenance_report(case_id: str, data: dict, report_text: str, media: list) -> Optional[dict]:
    """Persist report fields atomically without reopening a concurrently closed case."""
    with _FILE_LOCK:
        cases = _load(CASES_FILE, strict=True)
        if not isinstance(cases, list):
            raise ValueError("Case storage is unavailable.")
        for case in cases:
            if case.get("id") != case_id:
                continue
            for field in ("vehicle_type", "unit_number", "load_type", "location", "priority", "pickup",
                          "delivery", "comments", "setpoint", "current_temp", "temp_recorder"):
                case[field] = data.get("load" if field == "load_type" else field, "")
            case.update({"report_driver": data.get("driver", ""), "issue_text": data.get("issue", ""),
                         "report_text": report_text, "report_data": dict(data), "media": media,
                         "reported_at": now_iso(), "updated_at": now_iso()})
            if case.get("status") != "done" and not case.get("closed_at"):
                case["status"] = "reported"
                if not case.get("notes"):
                    case["notes"] = "case reported"
            _save(CASES_FILE, cases, strict=True)
            return dict(case)
    return None


def _close_record(case: dict, notes: Optional[str] = None) -> dict:
    """Shared Telegram/API close transition. Repeated closes preserve history."""
    if case.get("status") == "done":
        return case
    closed_at = now_iso()
    assigned = parse_timestamp(case.get("assigned_at"))
    closed = parse_timestamp(closed_at)
    case.update({"closed_at": closed_at, "updated_at": closed_at, "status": "done",
                 "resolution_secs": max(0, int((closed - assigned).total_seconds())) if assigned and closed else None})
    if notes is not None:
        case["notes"] = notes
    return case


def close_case(case_id: str, notes: Optional[str] = None, *,
               replaced_parts: Optional[list[str]] = None,
               parts_replaced: Optional[bool] = None) -> Optional[dict]:
    """Close atomically, retaining explicitly reported replaced part numbers for AI history.

    ``parts_replaced=None`` preserves the older API/desktop close contract.
    A repeated close must not overwrite the original repair history.
    """
    with _FILE_LOCK:
        cases = _load(CASES_FILE, strict=True)
        for case in cases:
            if case["id"] != case_id:
                continue
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
            _save(CASES_FILE, cases, strict=True)
            logger.info("Case %s closed; replaced part numbers: %s", case_id,
                        case.get("replaced_part_numbers") or "none/unspecified")
            return dict(case)
    return None


def is_maintenance_report(case: dict) -> bool:
    return bool(case.get("status") == "reported" or case.get("report_data") or
                (case.get("unit_number") and case.get("issue_text")))


def workspace_revision(case: dict) -> str:
    import hashlib
    from backend.core.case_snapshot import normalize
    return hashlib.sha256(json.dumps(normalize(case), sort_keys=True, default=str).encode()).hexdigest()


def workspace_write(actor: dict, payload: dict, case_id: Optional[str] = None) -> dict:
    """Authorized report writes under the same lock as Telegram handlers.

    Reads and writes fail closed; stale edits never overwrite a newer bot update.
    """
    with _FILE_LOCK:
        cases = _load(CASES_FILE, strict=True)
        if not isinstance(cases, list) or any(not isinstance(c, dict) for c in cases):
            raise ValueError("Case storage is unavailable.")
        now = now_iso()
        if case_id is None:
            for existing in cases:
                if (existing.get("workspace_request_id") == payload["request_id"] and
                        str(existing.get("created_by_id")) == str(actor["id"])):
                    return existing
            target = payload.get("target_agent") or {"id": actor["id"], "name": actor["name"],
                                                     "username": actor.get("username", "")}
            case = {"id": payload["id"], "driver_name": payload["driver"], "driver_username": "",
                    "group_name": payload.get("group", ""), "description": payload["issue"],
                    "opened_at": now, "reported_at": now, "updated_at": now, "assigned_at": now,
                    "closed_at": None, "agent_id": target["id"], "agent_name": target["name"],
                    "agent_username": target.get("username", ""), "status": "reported",
                    "vehicle_type": payload["vehicle_type"], "unit_number": payload["unit_number"],
                    "report_driver": payload["driver"], "issue_text": payload["issue"],
                    "location": payload.get("location", ""), "priority": payload["priority"],
                    "load_type": payload.get("load_type", ""),
                    "pickup": payload.get("pickup", ""), "delivery": payload.get("delivery", ""),
                    "setpoint": payload.get("setpoint", ""), "current_temp": payload.get("current_temp", ""),
                    "temp_recorder": payload.get("temp_recorder", ""),
                    "part_number": payload.get("part_number", ""),
                    "comments": payload.get("comments", ""), "notes": None, "media": [],
                    "source": "workspace", "created_by_id": actor["id"],
                    "workspace_request_id": payload["request_id"], "report_data": {**payload, "load": payload.get("load_type", "")}}
            cases.append(case)
        else:
            case = next((c for c in cases if c.get("id") == case_id), None)
            if not case:
                raise LookupError("Case not found.")
            if payload["action"] not in ("reassign", "stage", "move") and not is_maintenance_report(case):
                raise LookupError("Maintenance report not found. Awaiting Telegram report.")
            if actor["role"] not in ("developer", "super_admin") and str(case.get("agent_id")) != str(actor["id"]):
                raise PermissionError("Only the assigned agent or a manager can update this case.")
            # Close is idempotent, including a retry after a lost response.
            if payload["action"] == "close" and case.get("status") == "done":
                return case
            if payload.get("revision") != workspace_revision(case):
                raise ValueError("This report changed. Refresh its details before saving again.")
            if case.get("status") not in ("open", "assigned", "reported") or case.get("closed_at"):
                raise ValueError("This report is already closed.")
            if payload["action"] == "close":
                _close_record(case)
                case.update({"closed_by_id": actor["id"], "closed_by_name": actor["name"]})
            elif payload["action"] == "note":
                case.setdefault("workspace_notes", []).append({"text": payload["text"],
                    "author": actor["name"], "created_at": now})
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
                if str(case.get("agent_id")) == str(target["id"]) and (payload["action"] != "move" or (case.get("board_stage") or "") == payload["stage"]):
                    return dict(case)
                previous = case.get("agent_name") or "Unassigned"
                owner_changed = str(case.get("agent_id")) != str(target["id"])
                if owner_changed:
                    case.update({"agent_id": target["id"], "agent_name": target["name"],
                                 "agent_username": target.get("username", ""), "assigned_at": now,
                                 "reassigned": True})
                    if case.get("status") == "open":
                        case["status"] = "assigned"
                    case.setdefault("workspace_history", []).append({"action": "reassign", "author": actor["name"],
                        "from": previous, "to": target["name"], "created_at": now})
                if payload["action"] == "move" and (case.get("board_stage") or "") != payload["stage"]:
                    old_stage = case.get("board_stage") or ""
                    case["board_stage"] = payload["stage"]
                    case.setdefault("workspace_history", []).append({"action": "stage", "author": actor["name"],
                        "from": old_stage, "to": payload["stage"], "created_at": now})
            elif payload["action"] == "stage":
                previous = case.get("board_stage") or ""
                case["board_stage"] = payload["stage"]
                case.setdefault("workspace_history", []).append({"action": "stage", "author": actor["name"],
                    "from": previous, "to": payload["stage"], "created_at": now})
            elif payload["action"] == "edit":
                fields = dict(payload["fields"])
                # Keep the stored case consistent with Telegram load rules.
                new_load = fields.get("load_type", case.get("load_type") or (case.get("report_data") or {}).get("load", ""))
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
                case.setdefault("workspace_history", []).append({"action": payload["action"],
                    "author": actor["name"], "created_at": now})
        _save(CASES_FILE, cases, strict=True)
        return dict(case)


def mark_missed(case_id: str) -> None:
    with _FILE_LOCK:
        cases = _load(CASES_FILE)
        for case in cases:
            # Never turn an already-assigned case into a missed one.
            if case["id"] == case_id and case["status"] == "open" and case.get("agent_id") is None:
                case["status"] = "missed"
                _save(CASES_FILE, cases)
                return


def set_report_msg_id(case_id: str, msg_id: int) -> None:
    with _FILE_LOCK:
        cases = _load(CASES_FILE)
        for case in cases:
            if case["id"] == case_id:
                case["report_msg_id"] = msg_id
                _save(CASES_FILE, cases)
                return


# ── Cases — read ──────────────────────────────────────────────────────────────

def get_case(case_id: str) -> Optional[dict]:
    for case in _load(CASES_FILE):
        if case["id"] == case_id:
            return case
    return None


def get_cases_for_agent_today(agent_id: int) -> list[dict]:
    today = today_str()
    return [
        c for c in _load(CASES_FILE)
        if c.get("agent_id") == agent_id
        and chicago_date_str(c.get("assigned_at")) == today
    ]


def get_all_cases_for_agent(agent_id: int) -> list[dict]:
    cases = [c for c in _load(CASES_FILE) if c.get("agent_id") == agent_id]
    return sorted(cases, key=lambda c: c.get("opened_at", ""), reverse=True)


def get_active_case_for_agent(agent_id: int) -> Optional[dict]:
    active = [
        c for c in _load(CASES_FILE)
        if c.get("agent_id") == agent_id and c["status"] in ("assigned", "reported")
    ]
    return active[-1] if active else None


def get_cases_today() -> list[dict]:
    today = today_str()
    return [c for c in _load(CASES_FILE) if chicago_date_str(c.get("opened_at")) == today]



def get_cases_between(start, end) -> list[dict]:
    """Return cases opened in the half-open UTC-aware interval [start, end)."""
    rows = []
    for case in _load(CASES_FILE):
        opened = parse_timestamp(case.get("opened_at"))
        if opened and start <= opened < end:
            rows.append(case)
    return rows


def get_cases_this_week() -> list[dict]:
    start = week_start_str()
    return [c for c in _load(CASES_FILE) if chicago_date_str(c.get("opened_at")) >= start]


def get_all_cases() -> list[dict]:
    return sorted(_load(CASES_FILE), key=lambda c: c.get("opened_at", ""), reverse=True)


def get_untouched_unassigned_cases() -> list[dict]:
    """Return only cases that have never been claimed or otherwise handled.

    Deliberately strict: assigned/reported/done/missed cases are excluded, as
    are records with any assignment/closure/report residue. This keeps the
    /unassigned recovery command from resurfacing an active or previously
    touched case.
    """
    untouched = [
        c for c in _load(CASES_FILE)
        if c.get("status") == "open"
        and c.get("agent_id") is None
        and c.get("assigned_at") is None
        and c.get("closed_at") is None
        and c.get("notes") is None
        and c.get("report_msg_id") is None
    ]
    return sorted(untouched, key=lambda c: c.get("opened_at", ""), reverse=True)


# ── Active alerts — persisted so restarts don't lose unassigned alerts ────────

def save_active_alerts(alerts: dict) -> None:
    """Persist in-memory alert dict to disk."""
    serialisable = {}
    for aid, record in alerts.items():
        r = dict(record)
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
        if isinstance(r.get("last_escalated_at"), datetime):
            r["last_escalated_at"] = r["last_escalated_at"].isoformat()
        serialisable[aid] = r
    with _FILE_LOCK:
        _save(ALERTS_FILE, serialisable)


def load_active_alerts() -> dict:
    """Load persisted alerts back into memory on startup."""
    with _FILE_LOCK:
        raw = _load(ALERTS_FILE)
    if isinstance(raw, list):
        return {}          # old format guard
    return raw if isinstance(raw, dict) else {}


# ── async shims (called with await in some handlers) ─────────────────────────
# These are thin wrappers so handlers that use `await` still work fine.

async def async_get_active_case_for_agent(agent_id):
    return await asyncio.to_thread(get_active_case_for_agent, agent_id)

async def async_create_case(case_id, driver_name, driver_username, group_name, description):
    return await asyncio.to_thread(
        create_case, case_id, driver_name, driver_username, group_name, description
    )

async def async_assign_case(case_id, agent_id, agent_name, agent_username):
    return await asyncio.to_thread(assign_case, case_id, agent_id, agent_name, agent_username)

async def async_close_case(case_id, notes=None, *, replaced_parts=None, parts_replaced=None):
    return await asyncio.to_thread(close_case, case_id, notes,
                                   replaced_parts=replaced_parts, parts_replaced=parts_replaced)

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

async def ensure_indexes():
    """No-op — kept so bot.py import doesn't break."""
    pass


def workspace_attachment_write(actor, case_id, attachment=None, remove_id=None):
    """Persist attachment metadata with case authorization and a storage lock."""
    with _FILE_LOCK:
        cases = _load(CASES_FILE, strict=True)
        case = next((c for c in cases if c.get("id") == case_id and is_maintenance_report(c)), None)
        if not case:
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
        action = ("proof_removed" if remove_id else "proof_added") if is_proof else ("attachment_removed" if remove_id else "attachment_added")
        case.setdefault("workspace_history", []).append({"action": action, "author": actor["name"], "created_at": now_iso()})
        _save(CASES_FILE, cases, strict=True)
        return removed


def workspace_telegram_media_remove(actor, case_id: str, index: int):
    """Remove a Telegram media reference from a case with the same ACL as case files.

    This removes the attachment from Kurtex only; it cannot delete a Telegram message.
    """
    with _FILE_LOCK:
        cases = _load(CASES_FILE, strict=True)
        case = next((c for c in cases if c.get("id") == case_id and is_maintenance_report(c)), None)
        if not case:
            raise LookupError("Case not found.")
        if actor["role"] not in ("developer", "super_admin") and str(case.get("agent_id")) != str(actor["id"]):
            raise PermissionError("You cannot manage this case.")
        media = case.get("media") or []
        if not isinstance(media, list) or not 0 <= index < len(media):
            raise LookupError("Telegram attachment not found.")
        removed = media.pop(index)
        case["media"] = media
        case["updated_at"] = now_iso()
        case.setdefault("workspace_history", []).append({
            "action": "telegram_attachment_removed", "author": actor.get("name", "Team member"),
            "created_at": now_iso(), "media_type": str(removed[0] if isinstance(removed, (list, tuple)) and removed else removed.get("type", "") if isinstance(removed, dict) else "")[:30]
        })
        _save(CASES_FILE, cases, strict=True)
        return removed

def workspace_attachment_lookup(case_id, attachment_id):
    with _FILE_LOCK:
        cases = _load(CASES_FILE, strict=True)
        case = next((c for c in cases if c.get("id") == case_id and is_maintenance_report(c)), None)
        if not case:
            raise LookupError("Case not found.")
        item = next((x for x in case.get("workspace_attachments", []) if x.get("id") == attachment_id), None)
        if not item:
            raise LookupError("Attachment not found.")
        return dict(case), dict(item)
