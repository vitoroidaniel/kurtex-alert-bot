"""
storage/case_store.py
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger   = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
CASES_FILE = DATA_DIR / "cases.json"


def _load() -> list[dict]:
    if not CASES_FILE.exists():
        return []
    try:
        return json.loads(CASES_FILE.read_text())
    except Exception as e:
        logger.error(f"Failed to load cases: {e}")
        return []


def _save(cases: list[dict]) -> None:
    try:
        CASES_FILE.write_text(json.dumps(cases, indent=2, default=str))
    except Exception as e:
        logger.error(f"Failed to save cases: {e}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Write operations ──────────────────────────────────────────────────────────

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
        "response_secs":   None,
        "resolution_secs": None,
    }
    cases = _load()
    cases.append(case)
    _save(cases)
    logger.info(f"Case {case_id} created")
    return case


def assign_case(case_id: str, agent_id: int, agent_name: str, agent_username: Optional[str]) -> Optional[dict]:
    cases = _load()
    for case in cases:
        if case["id"] == case_id:
            assigned_at = now_iso()
            opened_dt   = datetime.fromisoformat(case["opened_at"])
            assigned_dt = datetime.fromisoformat(assigned_at)
            response_secs = int((assigned_dt - opened_dt).total_seconds())

            case.update({
                "assigned_at":    assigned_at,
                "agent_id":       agent_id,
                "agent_name":     agent_name,
                "agent_username": agent_username,
                "status":         "assigned",
                "response_secs":  response_secs,
            })
            _save(cases)
            logger.info(f"Case {case_id} assigned to {agent_name} (response: {response_secs}s)")
            return case
    logger.warning(f"assign_case: case {case_id} not found")
    return None


def report_case(case_id: str, notes: Optional[str] = "case reported") -> Optional[dict]:
    """Mark case as reported — stays active in /mycases until agent solves it."""
    cases = _load()
    for case in cases:
        if case["id"] == case_id:
            case.update({
                "status": "reported",
                "notes":  notes,
            })
            _save(cases)
            logger.info(f"Case {case_id} marked as reported")
            return case
    logger.warning(f"report_case: case {case_id} not found")
    return None


def close_case(case_id: str, notes: Optional[str] = None) -> Optional[dict]:
    cases = _load()
    for case in cases:
        if case["id"] == case_id:
            closed_at = now_iso()
            resolution_secs = None
            if case.get("assigned_at"):
                assigned_dt = datetime.fromisoformat(case["assigned_at"])
                closed_dt   = datetime.fromisoformat(closed_at)
                resolution_secs = int((closed_dt - assigned_dt).total_seconds())

            case.update({
                "closed_at":       closed_at,
                "status":          "done",
                "notes":           notes,
                "resolution_secs": resolution_secs,
            })
            _save(cases)
            logger.info(f"Case {case_id} closed")
            return case
    logger.warning(f"close_case: case {case_id} not found")
    return None


def mark_missed(case_id: str) -> None:
    cases = _load()
    for case in cases:
        if case["id"] == case_id and case["status"] == "open":
            case["status"] = "missed"
            _save(cases)
            logger.info(f"Case {case_id} marked missed")
            return


# ── Read operations ───────────────────────────────────────────────────────────

def get_case(case_id: str) -> Optional[dict]:
    for case in _load():
        if case["id"] == case_id:
            return case
    return None


def get_cases_for_agent_today(agent_id: int) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    return [
        c for c in _load()
        if c.get("agent_id") == agent_id
        and (c.get("assigned_at") or "").startswith(today)
    ]


def get_all_cases_for_agent(agent_id: int) -> list[dict]:
    return [c for c in _load() if c.get("agent_id") == agent_id]


def get_active_case_for_agent(agent_id: int) -> Optional[dict]:
    """Returns the most recent active (assigned or reported) case for this agent."""
    active = [
        c for c in _load()
        if c.get("agent_id") == agent_id and c["status"] in ("assigned", "reported")
    ]
    return active[-1] if active else None


def get_cases_today() -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    return [c for c in _load() if c.get("opened_at", "").startswith(today)]


def get_cases_this_week() -> list[dict]:
    from datetime import timedelta
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=now.weekday())).date().isoformat()
    return [c for c in _load() if c.get("opened_at", "") >= start]
