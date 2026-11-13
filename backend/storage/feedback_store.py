"""SQLite-backed Kurtex dashboard issues, feedback and developer conversations."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Optional

from backend.core.app_time import utc_now
from backend.storage import db

VALID_CATEGORIES = ("bug", "help", "suggestion", "ui", "performance")
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
        "diagnostics": json.loads(row["diagnostics"] or "{}"),
        "status": str(row["status"] or "open"),
        "admin_note": str(row["admin_note"] or ""),
        "internal_note": str(row["internal_note"] or ""),
        "resolution_reason": str(row["resolution_reason"] or ""),
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
    diagnostics: Optional[dict] = None,
) -> dict:
    """Create a user error/help report and notify dashboard bell feed."""
    clean_msg = (message or "").strip()
    if len(clean_msg) < 5:
        raise ValueError("Describe the issue in at least five characters.")
    if len(clean_msg) > 3000:
        raise ValueError("Report description must be 3000 characters or fewer.")
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
        "diagnostics": diagnostics or {},
        "status": "open",
        "admin_note": "",
        "internal_note": "",
        "resolution_reason": "",
        "resolved_by": "",
        "created_at": now,
        "updated_at": now,
    }
    with db.write_tx(sdir) as conn:
        conn.execute(
            """INSERT INTO bot_feedback(
                id, user_id, user_name, username, user_role, category,
                message, status, admin_note, resolved_by, created_at, updated_at, diagnostics
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                json.dumps(item["diagnostics"], ensure_ascii=False),
            ),
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
    internal_note: Optional[str] = None,
    resolution_reason: Optional[str] = None,
    resolved_by: Optional[str] = None,
) -> Optional[dict]:
    """Updates are atomic, and resolution permanently locks the report."""
    fid = (feedback_id or "").strip()
    if not fid:
        return None
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    now = utc_now().isoformat()
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT * FROM bot_feedback WHERE id=?", (fid,)).fetchone()
        if row is None:
            return None
        current = _row_to_dict(row)
        if current["status"] == "resolved":
            raise ValueError("This report is resolved and locked. It is view-only.")
        new_status = current["status"] if status is None else status
        public_message = current["admin_note"] if admin_note is None else str(admin_note).strip()[:3000]
        private_notes = current["internal_note"] if internal_note is None else str(internal_note).strip()[:3000]
        reason = current["resolution_reason"] if resolution_reason is None else str(resolution_reason).strip()[:1200]
        if new_status == "resolved" and not reason:
            raise ValueError("A resolution reason is required before closing the report.")
        actor = current["resolved_by"] if resolved_by is None else str(resolved_by).strip()[:120]
        resolver = actor if new_status == "resolved" else current["resolved_by"]
        conn.execute(
            """UPDATE bot_feedback
               SET status=?, admin_note=?, internal_note=?, resolution_reason=?, resolved_by=?, updated_at=?
               WHERE id=?""",
            (new_status, public_message, private_notes, reason, resolver, now, fid),
        )
        db.bump_version(conn, "feedback_version")
        updated = conn.execute("SELECT * FROM bot_feedback WHERE id=?", (fid,)).fetchone()
        return _row_to_dict(updated) if updated else None


def delete_feedback(feedback_id: str) -> bool:
    """Delete open tickets and their local files, keeping other tickets untouched."""
    import re
    fid = (feedback_id or "").strip()
    if not fid:
        return False
    files = []
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT status FROM bot_feedback WHERE id=?", (fid,)).fetchone()
        if row is None:
            return False
        if str(row["status"]) == "resolved":
            raise ValueError("This report is resolved and locked. It cannot be deleted.")
        files = [str(r["local_file_key"]) for r in conn.execute(
            "SELECT local_file_key FROM bot_feedback_messages WHERE feedback_id=?", (fid,)
        ).fetchall()]
        conn.execute("DELETE FROM bot_feedback_messages WHERE feedback_id=?", (fid,))
        cur = conn.execute("DELETE FROM bot_feedback WHERE id=?", (fid,))
        if cur.rowcount == 0:
            return False
        db.bump_version(conn, "feedback_version")
    path = _storage_dir() / "feedback_uploads"
    for name in files:
        if re.fullmatch(r"[0-9a-f]{40}\.(?:png|jpg|webp|pdf)", name):
            (path / name).unlink(missing_ok=True)
    return True


async def async_create_feedback(**kwargs) -> dict:
    return await asyncio.to_thread(create_feedback, **kwargs)


async def async_list_feedback(status: Optional[str] = None, limit: int = 200) -> dict:
    return await asyncio.to_thread(list_feedback, status, limit)


# Report conversations store only Telegram file identifiers. File bytes remain on
# Telegram; browser access is through an authenticated, token-hiding proxy.
def list_my_feedback(user_id: int, limit: int = 12) -> list[dict]:
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            "SELECT * FROM bot_feedback WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (int(user_id), min(max(int(limit), 1), 50)),
        ).fetchall()
    return [{k: item[k] for k in ("id", "user_id", "user_name", "username", "category", "message", "status", "created_at", "updated_at", "admin_note", "resolution_reason", "resolved_by")}
            for item in (_row_to_dict(row) for row in rows)]


def list_feedback_messages(feedback_id: str, limit: int = 100) -> list[dict]:
    """Only call after checking ownership or manager access."""
    with db.read_conn(_storage_dir()) as conn:
        rows = conn.execute(
            "SELECT id, feedback_id, author_name, sender_role, message, media_type, file_name, mime_type, created_at "
            "FROM bot_feedback_messages WHERE feedback_id=? ORDER BY id DESC LIMIT ?",
            (feedback_id, min(max(int(limit), 1), 200)),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]


def get_feedback_media(feedback_id: str, message_id: int) -> Optional[dict]:
    """Only call after checking ownership/manager rights to the report."""
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute(
            "SELECT telegram_file_id, local_file_key, media_type, file_name, mime_type FROM bot_feedback_messages "
            "WHERE id=? AND feedback_id=? AND (telegram_file_id!='' OR local_file_key!='')",
            (int(message_id), feedback_id),
        ).fetchone()
        return dict(row) if row else None


def add_feedback_message(
    feedback_id: str, *, author_id: str, author_name: str, sender_role: str,
    message: str = "", media_type: str = "", telegram_file_id: str = "",
    file_name: str = "", mime_type: str = "", local_file_key: str = "",
) -> dict:
    if sender_role not in ("reporter", "manager"):
        raise ValueError("Unsupported message sender.")
    body = str(message or "").strip()[:3000]
    kind = str(media_type or "")
    if kind not in ("", "photo", "document") or (kind and not (telegram_file_id or local_file_key)):
        raise ValueError("Unsupported attachment.")
    if local_file_key and ("/" in local_file_key or "\\" in local_file_key or not local_file_key.isascii()):
        raise ValueError("Invalid file key.")
    if not body and not kind:
        raise ValueError("Message or attachment is required.")
    with db.write_tx(_storage_dir()) as conn:
        row = conn.execute("SELECT status, user_id FROM bot_feedback WHERE id=?", (feedback_id,)).fetchone()
        if not row:
            raise LookupError("Report not found.")
        if str(row["status"]) == "resolved":
            raise ValueError("Resolved reports are read-only.")
        if sender_role == "reporter" and int(author_id) != int(row["user_id"]):
            raise PermissionError("This report belongs to another user.")
        ts = utc_now().isoformat()
        cursor = conn.execute(
            "INSERT INTO bot_feedback_messages(feedback_id,author_id,author_name,sender_role,message,"
            "media_type,telegram_file_id,file_name,mime_type,created_at,local_file_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (feedback_id, str(author_id), str(author_name or "")[:120], sender_role, body, kind,
             str(telegram_file_id or "")[:400], str(file_name or "")[:160],
             str(mime_type or "")[:100], ts, str(local_file_key or "")[:130]),
        )
        conn.execute("UPDATE bot_feedback SET updated_at=? WHERE id=?", (ts, feedback_id))
        db.bump_version(conn, "feedback_version")
        return {"id": cursor.lastrowid, "feedback_id": feedback_id, "author_name": author_name[:120],
                "sender_role": sender_role, "message": body, "media_type": kind,
                "file_name": str(file_name or "")[:160], "mime_type": str(mime_type or "")[:100], "created_at": ts}


def get_feedback_by_user(feedback_id: str, user_id: int) -> Optional[dict]:
    """Scoped lookup prevents reporters from reading another user's issue."""
    with db.read_conn(_storage_dir()) as conn:
        row = conn.execute(
            "SELECT * FROM bot_feedback WHERE id=? AND user_id=?",
            (str(feedback_id), int(user_id)),
        ).fetchone()
    return _row_to_dict(row) if row else None


def notify_feedback_users(user_ids, *, title: str, message: str, feedback_id: str, kind: str) -> None:
    """Publish private dashboard bell notifications, not Telegram messages."""
    users = sorted({str(int(uid)) for uid in user_ids if str(uid).isdigit()})
    if not users:
        return
    created = utc_now().isoformat()
    with db.write_tx(_storage_dir()) as conn:
        for uid in users:
            nid = f"notification-issue-{secrets.token_hex(12)}"
            data = {
                "id": nid, "user": uid, "kind": "issue",
                "type": kind, "title": str(title)[:120],
                "message": str(message)[:180],
                "report_id": str(feedback_id), "seen": False,
                "created_at": created,
            }
            conn.execute(
                "INSERT INTO notifications(id,user_key,created_at,seen,data_json) VALUES(?,?,?,?,?)",
                (nid, uid, created, 0, json.dumps(data, ensure_ascii=False, separators=(",", ":"))),
            )
