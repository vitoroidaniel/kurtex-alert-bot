"""
storage/case_store.py — Volume-backed JSON storage.

All data lives in /app/data/ which must be a Railway Volume mount.
Files survive restarts and redeploys permanently.

  /app/data/cases.json         — all case records
  /app/data/active_alerts.json — in-flight alerts (rebuilt on startup)
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR     = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CASES_FILE   = DATA_DIR / "cases.json"
ALERTS_FILE  = DATA_DIR / "active_alerts.json"


# ── Atomic write helpers ──────────────────────────────────────────────────────

def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to load {path.name}: {e}")
        return []


def _save(path: Path, data: list[dict] | dict) -> None:
    """Atomic write — write to .tmp then replace to avoid corruption."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.error(f"Failed to save {path.name}: {e}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    cases = _load(CASES_FILE)
    cases.append(case)
    _save(CASES_FILE, cases)
    logger.info(f"Case {case_id} created")
    return case


def assign_case(case_id: str, agent_id: int, agent_name: str, agent_username: Optional[str]) -> Optional[dict]:
    cases = _load(CASES_FILE)
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
            _save(CASES_FILE, cases)
            logger.info(f"Case {case_id} assigned to {agent_name}")
            return case
    logger.warning(f"assign_case: {case_id} not found")
    return None


def report_case(case_id: str, notes: Optional[str] = "case reported") -> Optional[dict]:
    cases = _load(CASES_FILE)
    for case in cases:
        if case["id"] == case_id:
            case.update({"status": "reported", "notes": notes})
            _save(CASES_FILE, cases)
            return case
    return None


def close_case(case_id: str, notes: Optional[str] = None) -> Optional[dict]:
    cases = _load(CASES_FILE)
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
            _save(CASES_FILE, cases)
            logger.info(f"Case {case_id} closed")
            return case
    return None


def mark_missed(case_id: str) -> None:
    cases = _load(CASES_FILE)
    for case in cases:
        if case["id"] == case_id and case["status"] in ("open", "assigned"):
            case["status"] = "missed"
            _save(CASES_FILE, cases)
            return


def set_report_msg_id(case_id: str, msg_id: int) -> None:
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
    today = datetime.now(timezone.utc).date().isoformat()
    return [
        c for c in _load(CASES_FILE)
        if c.get("agent_id") == agent_id
        and (c.get("assigned_at") or "").startswith(today)
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
    today = datetime.now(timezone.utc).date().isoformat()
    return [c for c in _load(CASES_FILE) if c.get("opened_at", "").startswith(today)]


def get_cases_this_week() -> list[dict]:
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=now.weekday())).date().isoformat()
    return [c for c in _load(CASES_FILE) if c.get("opened_at", "") >= start]


def get_all_cases() -> list[dict]:
    return sorted(_load(CASES_FILE), key=lambda c: c.get("opened_at", ""), reverse=True)


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
    _save(ALERTS_FILE, serialisable)


def load_active_alerts() -> dict:
    """Load persisted alerts back into memory on startup."""
    raw = _load(ALERTS_FILE)
    if isinstance(raw, list):
        return {}          # old format guard
    return raw if isinstance(raw, dict) else {}


# ── async shims (called with await in some handlers) ─────────────────────────
# These are thin wrappers so handlers that use `await` still work fine.

async def async_get_active_case_for_agent(agent_id):
    return get_active_case_for_agent(agent_id)

async def async_create_case(case_id, driver_name, driver_username, group_name, description):
    return create_case(case_id, driver_name, driver_username, group_name, description)

async def async_assign_case(case_id, agent_id, agent_name, agent_username):
    return assign_case(case_id, agent_id, agent_name, agent_username)

async def async_close_case(case_id, notes=None):
    return close_case(case_id, notes)

async def async_mark_missed(case_id):
    return mark_missed(case_id)

async def async_get_case(case_id):
    return get_case(case_id)

async def async_get_cases_for_agent_today(agent_id):
    return get_cases_for_agent_today(agent_id)

async def async_get_all_cases_for_agent(agent_id):
    return get_all_cases_for_agent(agent_id)

async def async_get_cases_today():
    return get_cases_today()

async def async_get_cases_this_week():
    return get_cases_this_week()

async def async_set_report_msg_id(case_id, msg_id):
    return set_report_msg_id(case_id, msg_id)

async def ensure_indexes():
    """No-op — kept so bot.py import doesn't break."""
    pass
