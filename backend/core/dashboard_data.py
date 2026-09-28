"""Read-only snapshots for the dashboard; the Telegram bot remains the writer."""
import json
import logging
import math
import time
from threading import Lock

logger = logging.getLogger(__name__)


class DataUnavailable(RuntimeError):
    pass


class CaseSnapshot:
    def __init__(self):
        self.lock = Lock()
        self.path = self.signature = self.cases = None
        self.retry_at = 0

    def read(self, path):
        with self.lock:
            if path != self.path:
                self.path = path
                self.signature = self.cases = None
                self.retry_at = 0
            if time.monotonic() < self.retry_at:
                return self.fallback()
            try:
                stat = path.stat()
                signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if signature != self.signature:
                    with path.open(encoding="utf-8") as source:
                        rows = json.load(source)
                    if not isinstance(rows, list) or any(not isinstance(c, dict) for c in rows):
                        raise ValueError("Expected a list of case objects")
                    self.cases = tuple(normalize(c) for c in rows)
                    self.signature = signature
                self.retry_at = 0
                return list(self.cases), False
            except (OSError, ValueError, TypeError):
                logger.exception("Dashboard could not read cases.json")
                self.retry_at = time.monotonic() + 2
                return self.fallback()

    def fallback(self):
        if self.cases is None:
            raise DataUnavailable("Case storage is unavailable. Please retry.")
        return list(self.cases), True


def normalize(case):
    result = dict(case)
    for key in ("id", "driver_name", "group_name", "agent_name", "agent_username",
                "description", "notes", "opened_at", "closed_at", "assigned_at",
                "vehicle_type", "unit_number", "report_driver", "issue_text",
                "load_type", "priority", "pickup", "delivery", "location", "comments",
                "setpoint", "current_temp", "temp_recorder"):
        result[key] = str(result[key]) if result.get(key) is not None else ""
    result["status"] = str(result.get("status") or "open")
    for key in ("response_secs", "resolution_secs"):
        try:
            number = float(result.get(key))
            result[key] = number if math.isfinite(number) and number >= 0 else None
        except (ValueError, TypeError, OverflowError):
            result[key] = None
    return result
