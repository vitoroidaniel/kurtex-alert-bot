"""Rules for strict Test-lane auto-routing vs suspect-test identification."""
from __future__ import annotations

import re

TEST_LANE_STAGE = "test"

TESTING_GROUPS = {"testing", "test", "tests"}
_TEST_GROUP_RE = re.compile(r"\b(?:tests?|testing)\b", re.IGNORECASE)
_TEST_TOKEN_RE = re.compile(r"\b(?:test|testing)\b|#tests?\b|#testing\b", re.IGNORECASE)
_DUMMY_SINGLE_RE = re.compile(
    r"^(?:\[?tests?(?:ing)?\]?(?:[-_\s#]*\d+)?|dummy|asdf|qwerty|qwe|abc|xyz|xxx|na|n/?a|none|\d{1,2}|[a-z])$",
    re.IGNORECASE,
)
_PURE_TEST_WORD_RE = re.compile(r"^(?:tests?|testing|dummy|asdf|qwerty)$", re.IGNORECASE)
_FILLER_WORDS = {
    "this", "is", "a", "an", "just", "only", "the", "please", "pls",
    "ignore", "bot", "alert", "case", "issue", "report", "message",
    "msg", "for", "me", "my", "check", "quick", "sample", "practice",
}
_BENIGN_MECHANICAL_TEST_RE = re.compile(
    r"\b(?:pressure|load|leak|road|bench|battery|brake|voltage|amp|alternator|starter|"
    r"compression|emissions|smog|dot|self|ecm|diagnostic|system|coolant|oil|fuel|air|"
    r"def|dpf|regen|hydro|dye|smoke|vacuum|boost|flow|continuity|resistance|ohm|"
    r"multimeter|dyno)\s+tests?\b|\btest\s+(?:drive|driving|drove|run|running)\b",
    re.IGNORECASE,
)


def is_test_group_name(group_name: str) -> bool:
    """Return True when a Telegram group/chat name indicates a test group."""
    clean = str(group_name or "").strip().lower()
    if not clean:
        return False
    return clean in TESTING_GROUPS or bool(_TEST_GROUP_RE.search(clean))


def _is_dummy_token(value: str) -> bool:
    clean = " ".join(str(value or "").split()).strip()
    if not clean:
        return False
    return bool(_DUMMY_SINGLE_RE.match(clean))


def is_pure_test_text(value: str) -> bool:
    """Return True only when the text is purely test/dummy words without real fault details."""
    clean = str(value or "").strip()
    if not clean:
        return False
    if _PURE_TEST_WORD_RE.match(clean):
        return True
    tokens = re.findall(r"[a-z0-9]+", clean.lower())
    if not tokens:
        return False
    has_test_marker = any(_PURE_TEST_WORD_RE.match(t) for t in tokens)
    if not has_test_marker:
        return False
    for tok in tokens:
        if _PURE_TEST_WORD_RE.match(tok) or tok in _FILLER_WORDS or tok.isdigit():
            continue
        return False
    return True


def should_auto_move_to_test_lane(case: dict) -> bool:
    """Auto-move ONLY unambiguous dummy ('unit 1 / driver 1 / issue 1') or all-test reports."""
    if not isinstance(case, dict):
        return False
    unit = " ".join(str(case.get("unit_number") or case.get("unit") or "").split()).strip()
    driver = " ".join(str(case.get("report_driver") or case.get("driver_name") or case.get("driver") or "").split()).strip()
    issue = " ".join(str(case.get("issue_text") or case.get("issue") or case.get("description") or "").split()).strip()
    desc = " ".join(str(case.get("description") or "").split()).strip()

    populated = [v for v in (unit, driver, issue) if v and v not in {"-", "—"}]
    # e.g. unit="1", driver="1", issue="1" or unit="test", driver="test", issue="test"
    if unit and issue:
        u_low, d_low, i_low = unit.lower(), driver.lower(), issue.lower()
        if u_low == i_low and (not d_low or u_low == d_low) and (len(u_low) <= 8 or _is_dummy_token(unit)):
            return True
        if driver and _is_dummy_token(unit) and _is_dummy_token(driver) and _is_dummy_token(issue):
            return True
    elif len(populated) >= 2 and all(_is_dummy_token(v) for v in populated):
        return True

    # All-test issue/description with no real mechanical description (e.g. "test", "test test test", "this is a test")
    if issue and is_pure_test_text(issue):
        return True
    if not issue and desc and is_pure_test_text(desc):
        return True
    if unit and driver and is_pure_test_text(unit) and is_pure_test_text(driver):
        return True
    return False


def is_suspect_test_candidate(case: dict) -> tuple[bool, str]:
    """Identify possible test reports (e.g. from test group or containing partial test wording) without moving them."""
    if not isinstance(case, dict):
        return False, ""
    if should_auto_move_to_test_lane(case):
        return False, ""
    group_raw = " ".join(str(case.get("group_name") or case.get("group") or "").split()).strip()
    reasons = []
    if is_test_group_name(group_raw):
        reasons.append(f'reported from test group "{group_raw}"')
    suspicious_fields = []
    for key, label in (
        ("unit_number", "unit"),
        ("unit", "unit"),
        ("report_driver", "driver"),
        ("driver_name", "driver"),
        ("driver", "driver"),
        ("issue_text", "issue"),
        ("issue", "issue"),
        ("description", "description"),
        ("comments", "comments"),
    ):
        val = str(case.get(key) or "").strip()
        if not val:
            continue
        stripped = _BENIGN_MECHANICAL_TEST_RE.sub(" ", val)
        if _TEST_TOKEN_RE.search(stripped) or _PURE_TEST_WORD_RE.match(val):
            if label not in suspicious_fields:
                suspicious_fields.append(label)
    if suspicious_fields:
        reasons.append(f"contains test wording in {', '.join(suspicious_fields)}")
    if not reasons:
        return False, ""
    return True, " and ".join(reasons)


def is_auto_test_report(case: dict) -> bool:
    """History/strict test matcher: explicit flag or unambiguous dummy/all-test report."""
    if not isinstance(case, dict):
        return False
    if case.get("is_test") or case.get("testing") or case.get("is_testing"):
        return True
    return should_auto_move_to_test_lane(case)
