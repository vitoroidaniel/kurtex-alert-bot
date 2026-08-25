"""
storage/case_store.py — Volume-backed JSON storage.

All data lives in /app/data/ which must be a Railway Volume mount.
Files survive restarts and redeploys permanently.

  /app/data/cases.json         — all case records
  /app/data/active_alerts.json — in-flight alerts (rebuilt on startup)

Design notes
────────────
All file I/O is performed via ``asyncio.to_thread`` so the event loop is
never blocked by disk reads or writes.  Writes are serialized with a
module-level ``asyncio.Lock`` to prevent concurrent coroutines from
clobbering each other's changes during a read-modify-write cycle.

Reads benefit from an mtime-keyed in-memory cache.  When multiple admin
commands fire back-to-back (e.g. ``/report`` calls
``get_cases_today()`` + ``get_cases_this_week()`` + ``get_all_cases()``)
the JSON file is parsed at most once per mtime — subsequent calls within
the same mtime window return the cached list directly.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DATA_DIR     = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CASES_FILE   = DATA_DIR / "cases.json"
ALERTS_FILE  = DATA_DIR / "active_alerts.json"


# A "day" for reports runs 06:30 AM -> next-day 06:30 AM (America/Chicago).
REPORT_TZ     = ZoneInfo("America/Chicago")
REPORT_HOUR   = 6
REPORT_MINUTE = 30


# ── Cache & write lock ──────────────────────────────────────────────────────────

# path -> (mtime, data)
_cache: dict[Path, tuple[float, Any]] = {}

_write_lock: Optional[asyncio.Lock] = None


def _get_write_lock() -> asyncio.Lock:
    """Lazily create the write lock (avoids loop-binding at import time)."""
    global _write_lock
    if _write_lock is None:
        _write_lock = asyncio.Lock()
    return _write_lock


def _to_report_tz(value):
    """Parse a stored timestamp and convert it to America/Chicago.

    Naive values (legacy records saved without an offset) are assumed UTC.
    Returns None if the value is missing or unparsable.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(REPORT_TZ)


def report_window_start(at: datetime | None = None) -> datetime:
    """Start of the current daily report window, in America/Chicago.

    A report window runs 06:30 AM -> next-day 06:30 AM Chicago time. If `at`
    (default: now) is before 06:30 AM, the active window began YESTERDAY at
    06:30 AM; otherwise it began today at 06:30 AM.
    """
    ref = at if at is not None else datetime.now(REPORT_TZ)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=REPORT_TZ)
    start = ref.replace(hour=REPORT_HOUR, minute=REPORT_MINUTE, second=0, microsecond=0)
    if ref < start:
        start -= timedelta(days=1)
    return start


# ── Internal I/O primitives (sync — always called from asyncio.to_thread) ──────

def _load_sync(path: Path) -> list[dict]:
    """Synchronous JSON read — must be called via ``asyncio.to_thread``."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to load {path.name}: {e}")
        return []


def _save_sync(path: Path, data: list[dict] | dict) -> None:
    """Synchronous atomic write — must be called via ``asyncio.to_thread``.

    Writes to a ``.tmp`` sibling then atomically replaces the target
    file to avoid corruption on crash.
    """
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.error(f"Failed to save {path.name}: {e}")


def now_iso() -> str:
    """Sync UTC ISO-8601 timestamp string (pure CPU, no I/O)."""
    return datetime.now(timezone.utc).isoformat()


# ── Cached async read ───────────────────────────────────────────────────────────

async def _load(path: Path) -> Any:
    """Read *path* with an mtime-keyed in-memory cache.

    Fast path (cache hit): a synchronous ``stat()`` confirms the file
    hasn't changed since the last read — no thread hop, no JSON parsing.

    Slow path (cache miss): the actual file read + JSON parse is offloaded
    to a worker thread via ``asyncio.to_thread``.
    """
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        mtime = 0.0
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    # Cache miss — offload the potentially expensive JSON parse to a thread
    data = await asyncio.to_thread(_load_sync, path)
    _cache[path] = (mtime, data)
    return data


async def _write(path: Path, data: Any) -> None:
    """Persist *data* to *path* and refresh the cache.

    The caller **must** hold ``_get_write_lock()`` — this function does
    not acquire it again (avoids re-entrant lock deadlock).
    """
    await asyncio.to_thread(_save_sync, path, data)
    # Refresh cache with the new mtime so subsequent reads hit the cache
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        mtime = 0.0
    _cache[path] = (mtime, data)


async def _save(path: Path, data: Any) -> None:
    """Public write entry point — acquires the lock then delegates."""
    async with _get_write_lock():
        await _write(path, data)


# ── Cases — write ─────────────────────────────────────────────────────────────

async def create_case(
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
    async with _get_write_lock():
        cases = await _load(CASES_FILE)
        cases.append(case)
        await _write(CASES_FILE, cases)
    logger.info(f"Case {case_id} created")
    return case


async def assign_case(
    case_id: str,
    agent_id: int,
    agent_name: str,
    agent_username: Optional[str],
    reassigned: bool = False,
) -> Optional[dict]:
    """Assign *case_id* to *agent_id*.

    If ``reassigned`` is True the ``reassigned`` flag is set on the
    case record in the same locked write, eliminating a second
    load/mutate/save cycle from the caller.
    """
    async with _get_write_lock():
        cases = await _load(CASES_FILE)
        for case in cases:
            if case["id"] == case_id:
                assigned_at   = now_iso()
                response_secs = int(
                    (datetime.fromisoformat(assigned_at) - datetime.fromisoformat(case["opened_at"])).total_seconds()
                )
                case.update({
                    "assigned_at":    assigned_at,
                    "agent_id":       agent_id,
                    "agent_name":     agent_name,
                    "agent_username": agent_username,
                    "status":         "assigned",
                    "response_secs":  response_secs,
                })
                if reassigned:
                    case["reassigned"] = True
                await _write(CASES_FILE, cases)
                logger.info(f"Case {case_id} assigned to {agent_name}")
                return case
    logger.warning(f"assign_case: {case_id} not found")
    return None


async def report_case(
    case_id: str,
    notes: Optional[str] = "case reported",
    fleet_data: Optional[dict] = None,
) -> Optional[dict]:
    """Mark *case_id* as ``reported``.

    ``fleet_data`` — when supplied — is merged into the case record in
    the **same** locked write so callers no longer need a second
    load/mutate/save cycle to stash dashboard analytics fields.
    """
    async with _get_write_lock():
        cases = await _load(CASES_FILE)
        for case in cases:
            if case["id"] == case_id:
                case.update({"status": "reported", "notes": notes})
                if fleet_data:
                    case.update(fleet_data)
                await _write(CASES_FILE, cases)
                return case
    return None


async def close_case(case_id: str, notes: Optional[str] = None) -> Optional[dict]:
    async with _get_write_lock():
        cases = await _load(CASES_FILE)
        for case in cases:
            if case["id"] == case_id:
                closed_at       = now_iso()
                resolution_secs = None
                if case.get("assigned_at"):
                    resolution_secs = int(
                        (datetime.fromisoformat(closed_at) - datetime.fromisoformat(case["assigned_at"])).total_seconds()
                    )
                case.update({
                    "closed_at":       closed_at,
                    "status":          "done",
                    "notes":           notes,
                    "resolution_secs": resolution_secs,
                })
                await _write(CASES_FILE, cases)
                logger.info(f"Case {case_id} closed")
                return case
    return None


async def mark_missed(case_id: str) -> None:
    async with _get_write_lock():
        cases = await _load(CASES_FILE)
        for case in cases:
            if case["id"] == case_id and case["status"] in ("open", "assigned"):
                case["status"] = "missed"
                await _write(CASES_FILE, cases)
                return


async def set_report_msg_id(case_id: str, msg_id: int) -> None:
    async with _get_write_lock():
        cases = await _load(CASES_FILE)
        for case in cases:
            if case["id"] == case_id:
                case["report_msg_id"] = msg_id
                await _write(CASES_FILE, cases)
                return


# ── Cases — read ────────────────────────────────────────────────────────────────

async def get_case(case_id: str) -> Optional[dict]:
    for case in await _load(CASES_FILE):
        if case["id"] == case_id:
            return case
    return None


async def get_cases_for_agent_today(agent_id: int) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    cases = await _load(CASES_FILE)
    return [
        c for c in cases
        if c.get("agent_id") == agent_id
        and (c.get("assigned_at") or "").startswith(today)
    ]


async def get_all_cases_for_agent(agent_id: int) -> list[dict]:
    cases = await _load(CASES_FILE)
    filtered = [c for c in cases if c.get("agent_id") == agent_id]
    return sorted(filtered, key=lambda c: c.get("opened_at", ""), reverse=True)


async def get_active_case_for_agent(agent_id: int) -> Optional[dict]:
    cases = await _load(CASES_FILE)
    active = [
        c for c in cases
        if c.get("agent_id") == agent_id and c["status"] in ("assigned", "reported")
    ]
    return active[-1] if active else None


async def _cases_in_window(start, end) -> list[dict]:
    result = []
    for c in await _load(CASES_FILE):
        dt = _to_report_tz(c.get("opened_at"))
        if dt is not None and start <= dt < end:
            result.append(c)
    return result


async def get_cases_today() -> list[dict]:
    """Cases in the current 6:30 AM (Chicago) anchored day:
    [today's 6:30 AM, tomorrow 6:30 AM).  Used by /report and /missed.
    """
    start = report_window_start()
    return await _cases_in_window(start, start + timedelta(days=1))


async def get_completed_day_cases() -> list[dict]:
    """Cases in the 24h window that just closed at the most recent
    6:30 AM (Chicago): [yesterday 6:30 AM, today 6:30 AM).

    Used by the scheduled end-of-day daily report so it counts the full
    6:30 AM -> next-day 6:30 AM day that has just ended.
    """
    start = report_window_start() - timedelta(days=1)
    return await _cases_in_window(start, start + timedelta(days=1))


async def get_cases_this_week() -> list[dict]:
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=now.weekday())).date().isoformat()
    return [c for c in (await _load(CASES_FILE)) if c.get("opened_at", "") >= start]


async def get_all_cases() -> list[dict]:
    return sorted(
        await _load(CASES_FILE),
        key=lambda c: c.get("opened_at", ""),
        reverse=True,
    )


# ── Active alerts — persisted so restarts don't lose unassigned alerts ────────

async def save_active_alerts(alerts: dict) -> None:
    """Persist in-memory alert dict to disk."""
    serialisable = {}
    for aid, record in alerts.items():
        r = dict(record)
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
        if isinstance(r.get("last_escalated_at"), datetime):
            r["last_escalated_at"] = r["last_escalated_at"].isoformat()
        serialisable[aid] = r
    await _save(ALERTS_FILE, serialisable)


async def load_active_alerts() -> dict:
    """Load persisted alerts back into memory on startup."""
    raw = await _load(ALERTS_FILE)
    if isinstance(raw, list):
        return {}          # old format guard
    return raw if isinstance(raw, dict) else {}


# ── Backwards-compat entry point ────────────────────────────────────────────────

async def ensure_indexes() -> None:
    """No-op — kept so bot.py import doesn't break."""
    pass
