"""Rules for identifying test reports or test-group cases and auto-routing them to the Test lane."""
from __future__ import annotations

import re

TEST_LANE_STAGE = "test"

TESTING_GROUPS = {"testing", "test", "tests"}
_TEST_GROUP_RE = re.compile(r"\b(?:tests?|testing)\b", re.IGNORECASE)
_TEST_TOKEN_RE = re.compile(r"\b(?:test|testing)\b|#tests?\b|#testing\b", re.IGNORECASE)
_TEST_IDENTIFIER_RE = re.compile(
    r"^(?:\[?tests?(?:ing)?\]?(?:[-_\s#]*\d+)?|test\s+(?:case|issue|report|unit|driver)|dummy|asdf|qwerty)$",
    re.IGNORECASE,
)
_BENIGN_MECHANICAL_TEST_RE = re.compile(
    r"\b(?:pressure|load|leak|road|bench|battery|brake|voltage|amp|alternator|starter|"
    r"compression|emissions|smog|dot|self|ecm|diagnostic|system|coolant|oil|fuel|air|"
    r"def|dpf|regen|hydro|dye|smoke|vacuum|boost|flow|continuity|resistance|ohm|"
    r"multimeter|dyno)\s+tests?\b|\btest\s+(?:drive|driving|drove|run|running)\b",
    re.IGNORECASE,
)


def _has_test_text(raw: str, *, short_id_field: bool = False) -> bool:
    clean = str(raw or "").strip()
    if not clean:
        return False
    if _TEST_IDENTIFIER_RE.match(clean):
        return True
    stripped = _BENIGN_MECHANICAL_TEST_RE.sub(" ", clean)
    if _TEST_TOKEN_RE.search(stripped):
        return True
    if short_id_field and re.match(r"^test(?:ing)?[-_#\d]*$", clean, re.IGNORECASE):
        return True
    return False


def is_test_group_name(group_name: str) -> bool:
    """Return True when a Telegram group/chat name indicates a test group."""
    clean = str(group_name or "").strip().lower()
    if not clean:
        return False
    return clean in TESTING_GROUPS or bool(_TEST_GROUP_RE.search(clean))


def is_auto_test_report(case: dict) -> bool:
    """Return True when a case/report contains test text or comes from a test group."""
    if not isinstance(case, dict):
        return False
    if case.get("is_test") or case.get("testing") or case.get("is_testing"):
        return True
    if is_test_group_name(str(case.get("group_name") or case.get("group") or "")):
        return True
    for key in ("unit_number", "unit", "report_driver", "driver_name", "driver"):
        if _has_test_text(str(case.get(key) or ""), short_id_field=True):
            return True
    for key in ("issue_text", "issue", "description", "comments"):
        if _has_test_text(str(case.get(key) or ""), short_id_field=False):
            return True
    unit = str(case.get("unit_number") or case.get("unit") or "").strip().lower()
    driver = str(case.get("report_driver") or case.get("driver_name") or case.get("driver") or "").strip().lower()
    issue = str(case.get("issue_text") or case.get("description") or "").strip().lower()
    if unit and unit not in {"-", "—"} and unit == driver and (unit == issue or not issue or len(issue) <= 2):
        if len(unit) <= 3 or unit.isdigit():
            return True
    return False
