"""SQLite-backed storage for user bot bug reports, error reports, and help requests."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Optional

from backend.core.app_time import utc_now
from backend.storage import db

VALID_CATEGORIES = ("bug", "help", "suggestion")
VALID_STATUSES = ("open", "in_progress", "resolved")

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))


def _storage_dir() -> Path:
    env_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    target = env_dir if env_dir != Path("/app/data") else DATA_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def _row_to_dict(row) -> dict:
    return {
        "id": str(row["id"]),
        "user_id": int(row["user_id"]),
        "user_name": str(row["user_name"] or ""),
        "username": str(row["username"] or ""),
        "user_role": str(row["user_role"] or "agent"),
        "category": str(row["category"] or "bug"),
        "message": str(row["message"] or ""),
        "status": str(row["status"] or "open"),
        "admin_note": str(row["admin_note"] or ""),
        "resolved_by": str(row["resolved_by"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def create_feedback(
    *,
    user_id: int,
    user_name: str,
    username: str,
    user_role: str,
    category: str,
    message: str,
) -> dict:
    """Create a user error/help report and notify dashboard bell feed."""
    clean_msg = (message or "").strip()
    if not clean_msg:
        raise ValueError("Report message cannot be empty.")
    cat = category if category in VALID_CATEGORIES else "bug"
    now = utc_now().isoformat()
    fid = f"fb-{secrets.token_hex(4)}"
    clean_uname = (username or "").strip().lstrip("@")
    sdir = _storage_dir()

    item = {
        "id": fid,
        "user_id": int(user_id),
        "user_name": (user_name or f"User {user_id}").strip()[:120],
        "username": clean_uname[:80],
        "user_role": (user_role or "agent").strip()[:40],
        "category": cat,
        "message": clean_msg[:3000],
        "status": "open",
        "admin_note": "",
        "resolved_by": "",
        "created_at": now,
        "updated_at": now,
    }
    cat_label = {"bug": "Bot Error / Bug", "help": "Help Request", "suggestion": "Suggestion"}.get(cat, "Bot Report")
    notif_id = f"notif-fb-{fid}"
    notif_payload = {
        "id": notif_id,
        "user": "all",
        "type": "bot_feedback",
        "title": f"{cat_label} from {item['user_name']}",
        "message": clean_msg[:180],
        "case_id": "",
        "created_at": now,
        "seen": False,
    }

    with db.write_tx(sdir) as conn:
        conn.execute(
            """INSERT INTO bot_feedback(
                id, user_id, user_name, username, user_role, category,
                message, status, admin_note, resolved_by, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item["id"],
                item["user_id"],
                item["user_name"],
                item["username"],
                item["user_role"],
                item["category"],
                item["message"],
                item["status"],
                item["admin_note"],
                item["resolved_by"],
                item["created_at"],
                item["updated_at"],
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO notifications(id, user_key, created_at, seen, data_json) VALUES(?,?,?,?,?)",
            (notif_id, "all", now, 0, json.dumps(notif_payload, ensure_ascii=False, separators=(",", ":"))),
        )
        db.bump_version(conn, "feedback_version")
    return item


def get_feedback(feedback_id: str) -> Optional[dict]:
    fid = (feedback_id or "").strip()
    if not fid:
        return None
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute("SELECT * FROM bot_feedback WHERE id=?", (fid,)).fetchone()
        return _row_to_dict(row) if row else None


def list_feedback(status: Optional[str] = None, limit: int = 200) -> dict:
    sdir = _storage_dir()
    lim = max(1, min(int(limit or 200), 500))
    status_filter = status if status in VALID_STATUSES else None
    with db.read_conn(sdir) as conn:
        count_rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM bot_feedback GROUP BY status"
        ).fetchall()
        counts = {"open": 0, "in_progress": 0, "resolved": 0}
        for r in count_rows:
            if r["status"] in counts:
                counts[r["status"]] = int(r["cnt"])
        counts["all"] = counts["open"] + counts["in_progress"] + counts["resolved"]

        if status_filter:
            rows = conn.execute(
                "SELECT * FROM bot_feedback WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status_filter, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM bot_feedback
                   ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                            created_at DESC
                   LIMIT ?""",
                (lim,),
            ).fetchall()
        ver_row = conn.execute("SELECT value FROM meta WHERE key='feedback_version'").fetchone()
        ver = int(ver_row["value"]) if ver_row else 1
    return {"items": [_row_to_dict(r) for r in rows], "counts": counts, "version": ver}


def update_feedback(
    feedback_id: str,
    *,
    status: Optional[str] = None,
    admin_note: Optional[str] = None,
    resolved_by: Optional[str] = None,
) -> Optional[dict]:
    fid = (feedback_id or "").strip()
    if not fid:
        return None
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    sdir = _storage_dir()
    now = utc_now().isoformat()
    with db.write_tx(sdir) as conn:
        row = conn.execute("SELECT * FROM bot_feedback WHERE id=?", (fid,)).fetchone()
        if not row:
            return None
        current = _row_to_dict(row)
        new_status = status if status is not None else current["status"]
        new_note = current["admin_note"] if admin_note is None else str(admin_note).strip()[:3000]
        new_resolver = current["resolved_by"] if resolved_by is None else str(resolved_by).strip()[:120]
        conn.execute(
            """UPDATE bot_feedback
               SET status=?, admin_note=?, resolved_by=?, updated_at=?
               WHERE id=?""",
            (new_status, new_note, new_resolver, now, fid),
        )
        db.bump_version(conn, "feedback_version")
        updated = conn.execute("SELECT * FROM bot_feedback WHERE id=?", (fid,)).fetchone()
        return _row_to_dict(updated) if updated else None


def delete_feedback(feedback_id: str) -> bool:
    fid = (feedback_id or "").strip()
    if not fid:
        return False
    with db.write_tx(_storage_dir()) as conn:
        cur = conn.execute("DELETE FROM bot_feedback WHERE id=?", (fid,))
        if cur.rowcount == 0:
            return False
        db.bump_version(conn, "feedback_version")
        return True


async def async_create_feedback(**kwargs) -> dict:
    return await asyncio.to_thread(create_feedback, **kwargs)


async def async_list_feedback(status: Optional[str] = None, limit: int = 200) -> dict:
    return await asyncio.to_thread(list_feedback, status, limit)
