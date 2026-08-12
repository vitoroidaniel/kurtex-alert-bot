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


# ── FAST IN-PLACE UPDATE (new!) ──────────────────────────────────────────────

def _update_case_inplace(case_id: str, updater: callable) -> Optional[dict]:
    """
    Read cases.json line by line, find the matching case, apply `updater`,
    and write only that line back. This is O(n) but never rewrites the whole file.
    Returns the updated case dict if found, else None.
    """
    if not CASES_FILE.exists():
        return None

    updated_case = None
    found = False
    lines = []

    try:
        with open(CASES_FILE, "r", encoding="utf-8") as f:
            # Read all lines (we'll rebuild the file with the same formatting)
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Failed to read {CASES_FILE} for in-place update: {e}")
        return None

    # Find the line containing the case — we'll rewrite it
    for i, line in enumerate(lines):
        try:
            if not line.strip():
                continue
            obj = json.loads(line.rstrip(",\n"))
            if obj.get("id") == case_id:
                updated_case = updater(obj)
                # Replace the line with the updated JSON (keep the comma if needed)
                # We'll keep the same indentation style: 2 spaces, with a comma at the end if not the last item
                new_line = json.dumps(obj, default=str)
                # Check if the original line had a trailing comma (assume it's a list item)
                if line.rstrip().endswith(","):
                    new_line += ","
                lines[i] = new_line + "\n"
                found = True
                break
        except Exception:
            continue

    if not found:
        return None

    # Write back the modified file
    try:
        tmp = CASES_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        tmp.replace(CASES_FILE)
    except Exception as e:
        logger.error(f"Failed to write in-place update for {case_id}: {e}")
        return None

    return updated_case


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Cases — write (now using in‑place update where possible) ─────────────────

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
    def updater(case):
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
        return case

    updated = _update_case_inplace(case_id, updater)
    if updated:
        logger.info(f"Case {case_id} assigned to {agent_name}")
    else:
        logger.warning(f"assign_case: {case_id} not found")
    return updated


def report_case(case_id: str, notes: Optional[str] = "case reported") -> Optional[dict]:
    def updater(case):
        case.update({"status": "reported", "notes": notes})
        return case

    updated = _update_case_inplace(case_id, updater)
    if updated:
        logger.info(f"Case {case_id} reported")
    else:
        logger.warning(f"report_case: {case_id} not found")
    return updated


def close_case(case_id: str, notes: Optional[str] = None) -> Optional[dict]:
    def updater(case):
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
        return case

    updated = _update_case_inplace(case_id, updater)
    if updated:
        logger.info(f"Case {case_id} closed")
    else:
        logger.warning(f"close_case: {case_id} not found")
    return updated


def mark_missed(case_id: str) -> None:
    def updater(case):
        if case["status"] in ("open", "assigned"):
            case["status"] = "missed"
        return case

    _update_case_inplace(case_id, updater)


def set_report_msg_id(case_id: str, msg_id: int) -> None:
    def updater(case):
        case["report_msg_id"] = msg_id
        return case

    _update_case_inplace(case_id, updater)


# ── Cases — read (unchanged) ─────────────────────────────────────────────────

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
