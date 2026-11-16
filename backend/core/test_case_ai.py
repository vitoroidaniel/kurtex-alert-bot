"""AI & heuristic suspect-test detector that adds an internal System note without auto-moving."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request

from backend.core.test_case_rules import (
    TEST_LANE_STAGE,
    is_suspect_test_candidate,
    should_auto_move_to_test_lane,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "@cf/meta/llama-4-scout-17b-16e-instruct"

_SYSTEM_PROMPT = (
    "You classify incoming commercial fleet maintenance reports (trucks, trailers, reefers). "
    "Real breakdowns are sometimes accidentally sent in a 'Test' chat group, and real shop notes mention "
    "'pressure test', 'road test', 'battery load test', or 'test drive'. "
    "Return ONLY valid JSON: {\"verdict\": \"real\" | \"maybe_test\", \"reason\": \"concise 1-sentence reason\"}. "
    "Use \"maybe_test\" if the report came from a test group or looks like a possible test/practice/sample report, "
    "even if it has some real-looking details. Use \"real\" when it is clearly an actual fleet breakdown from a normal group."
)


def _ai_configured() -> tuple[str, str, str]:
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    model = os.getenv("KURTEX_AI_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    return account, token, model


def classify_suspect_test_with_ai(case: dict) -> tuple[str, str] | None:
    """Query Workers AI to check if a report might be a test case. Returns (verdict, reason) or None."""
    account, token, model = _ai_configured()
    if not account or not token:
        return None
    summary = {
        "group_name": str(case.get("group_name") or case.get("group") or "")[:120],
        "unit_number": str(case.get("unit_number") or case.get("unit") or "")[:80],
        "vehicle_type": str(case.get("vehicle_type") or "")[:40],
        "driver": str(case.get("report_driver") or case.get("driver_name") or case.get("driver") or "")[:120],
        "issue": str(case.get("issue_text") or case.get("issue") or case.get("description") or "")[:500],
        "comments": str(case.get("comments") or "")[:300],
        "location": str(case.get("location") or "")[:200],
        "source": str(case.get("source") or "telegram")[:40],
    }
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    body = {
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(summary, ensure_ascii=False)},
        ],
        "max_tokens": 120,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("Suspect test AI check skipped: %s", exc)
        return None

    res = (payload or {}).get("result") if isinstance(payload, dict) else payload
    raw_text = ""
    if isinstance(res, dict):
        raw_text = str(res.get("response") or res.get("text") or res.get("output_text") or "")
    elif isinstance(res, str):
        raw_text = res
    if not raw_text:
        return None
    m = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return None
    verdict = str(parsed.get("verdict") or "").strip().lower()
    reason = " ".join(str(parsed.get("reason") or "").split()).strip()[:240]
    if verdict in ("maybe_test", "likely_test", "test"):
        return ("maybe_test", reason or "Flagged by AI as a possible test case.")
    if verdict == "real":
        return ("real", reason)
    return None


def build_suspect_system_note_text(reason: str, *, ai_verified: bool = False) -> str:
    clean_reason = " ".join(str(reason or "").split()).strip().rstrip(".")
    prefix = "AI check — possible test case" if ai_verified else "Possible test case"
    detail = f": {clean_reason}" if clean_reason else ""
    return (
        f"{prefix}{detail}. Not auto-moved so real breakdowns stay visible — "
        "review and move to the Test lane if needed."
    )


def evaluate_and_annotate_case(case_id: str, expected_storage_dir=None) -> bool:
    """Run AI / heuristic check for case_id and append an internal System note if maybe test."""
    from backend.storage import case_store

    if expected_storage_dir is not None:
        try:
            if not expected_storage_dir.exists():
                return False
            if case_store._storage_dir().resolve() != expected_storage_dir.resolve():
                return False
        except Exception:
            return False

    case = case_store.get_case(case_id)
    if not isinstance(case, dict):
        return False
    if case.get("status") == "done" or case.get("closed_at"):
        return False
    if str(case.get("board_stage") or "").strip().lower() == TEST_LANE_STAGE:
        return False
    if should_auto_move_to_test_lane(case):
        return False
    if any(
        isinstance(n, dict) and not n.get("deleted_at") and n.get("kind") == "test_suspect"
        for n in (case.get("workspace_notes") or [])
    ):
        return False

    ai_result = classify_suspect_test_with_ai(case)
    if ai_result is not None:
        verdict, reason = ai_result
        if verdict == "maybe_test":
            return bool(
                case_store.add_system_test_note(
                    case_id,
                    build_suspect_system_note_text(reason, ai_verified=True),
                )
            )
        return False

    suspect, reason = is_suspect_test_candidate(case)
    if suspect:
        return bool(
            case_store.add_system_test_note(
                case_id,
                build_suspect_system_note_text(reason, ai_verified=False),
            )
        )
    return False


def schedule_test_case_review(case_id: str, case_snapshot: dict | None = None, storage_dir=None) -> None:
    """Annotate suspect test reports with an internal System note without auto-moving."""
    if not case_id:
        return
    if isinstance(case_snapshot, dict):
        if case_snapshot.get("status") == "done" or case_snapshot.get("closed_at"):
            return
        if str(case_snapshot.get("board_stage") or "").strip().lower() == TEST_LANE_STAGE:
            return
        if should_auto_move_to_test_lane(case_snapshot):
            return

    account, token, _ = _ai_configured()
    if account and token:
        t = threading.Thread(
            target=evaluate_and_annotate_case,
            args=(str(case_id), storage_dir),
            name=f"test-case-ai-{str(case_id)[:8]}",
            daemon=True,
        )
        t.start()
        return

    # Without AI credentials, run deterministic suspect check synchronously.
    if isinstance(case_snapshot, dict):
        suspect, reason = is_suspect_test_candidate(case_snapshot)
        if suspect:
            from backend.storage import case_store

            case_store.add_system_test_note(
                str(case_id),
                build_suspect_system_note_text(reason, ai_verified=False),
            )
