"""Shared SQLite (WAL mode) persistence engine for Kurtex Bot & API.

Replaces full-file JSON rewrites and cross-process fcntl file locks with indexed
ACID transactions on DATA_DIR/kurtex.sqlite3, while automatically performing a
lossless one-time import of any legacy JSON files present on the volume.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_INIT_LOCK = threading.RLock()
_INITIALIZED_DBS: set[str] = set()
_THREAD_LOCAL = threading.local()
_ALL_CONNS_LOCK = threading.Lock()
_ALL_CONNS: set[sqlite3.Connection] = set()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id                   TEXT PRIMARY KEY,
    status               TEXT NOT NULL DEFAULT 'open',
    agent_id             INTEGER,
    opened_at            TEXT NOT NULL DEFAULT '',
    assigned_at          TEXT,
    reported_at          TEXT,
    closed_at            TEXT,
    updated_at           TEXT,
    report_msg_id        INTEGER,
    unit_number          TEXT NOT NULL DEFAULT '',
    driver_name          TEXT NOT NULL DEFAULT '',
    group_name           TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL DEFAULT 'telegram',
    workspace_request_id TEXT,
    created_by_id        TEXT,
    data_json            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_agent_status ON cases(agent_id, status, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_cases_status_opened ON cases(status, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_cases_opened ON cases(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_cases_reported ON cases(status, reported_at DESC);
CREATE INDEX IF NOT EXISTS idx_cases_req ON cases(workspace_request_id, created_by_id);

CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    username   TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'agent',
    extra_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS shifts (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time   TEXT NOT NULL,
    days_json  TEXT NOT NULL,
    timezone   TEXT NOT NULL DEFAULT 'America/Chicago',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_shifts (
    user_id  INTEGER NOT NULL,
    shift_id TEXT NOT NULL,
    PRIMARY KEY (user_id, shift_id),
    FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_user_shifts_shift ON user_shifts(shift_id);

CREATE TABLE IF NOT EXISTS active_alerts (
    alert_id    TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT '',
    taken_by_id INTEGER,
    data_json   TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS preferences (
    user_id    TEXT PRIMARY KEY,
    data_json  TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role       TEXT PRIMARY KEY,
    pages_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id         TEXT PRIMARY KEY,
    user_key   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    seen       INTEGER NOT NULL DEFAULT 0,
    data_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_key, created_at DESC);

CREATE TABLE IF NOT EXISTS kv_state (
    key       TEXT PRIMARY KEY,
    version   INTEGER NOT NULL DEFAULT 0,
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_feedback (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    user_role   TEXT NOT NULL DEFAULT 'agent',
    category    TEXT NOT NULL DEFAULT 'bug',
    message     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    admin_note  TEXT NOT NULL DEFAULT '',
    internal_note TEXT NOT NULL DEFAULT '',
    resolution_reason TEXT NOT NULL DEFAULT '',
    resolved_by TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    diagnostics TEXT NOT NULL DEFAULT '{}',
    assigned_to TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_bot_feedback_status ON bot_feedback(status, created_at DESC);
"""


def get_data_dir(override: Path | str | None = None) -> Path:
    if override is not None:
        p = Path(override)
    else:
        p = Path(os.getenv("DATA_DIR", "/app/data"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path_for(data_dir: Path | str | None = None) -> Path:
    return get_data_dir(data_dir) / "kurtex.sqlite3"


def _open_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(db_path),
        timeout=20.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def close_thread_connections() -> None:
    return None


def close_all_connections() -> None:
    import gc
    gc.collect()
    return None


def _extract_case_columns(case: dict) -> tuple:
    agent_id = case.get("agent_id")
    try:
        agent_id_val = int(agent_id) if agent_id is not None and str(agent_id).strip() != "" else None
    except (TypeError, ValueError):
        agent_id_val = None
    msg_id = case.get("report_msg_id")
    try:
        msg_id_val = int(msg_id) if msg_id is not None else None
    except (TypeError, ValueError):
        msg_id_val = None
    return (
        str(case.get("id") or ""),
        str(case.get("status") or "open"),
        agent_id_val,
        str(case.get("opened_at") or ""),
        case.get("assigned_at"),
        case.get("reported_at"),
        case.get("closed_at"),
        case.get("updated_at"),
        msg_id_val,
        str(case.get("unit_number") or ""),
        str(case.get("driver_name") or ""),
        str(case.get("group_name") or ""),
        str(case.get("source") or "telegram"),
        case.get("workspace_request_id"),
        str(case["created_by_id"]) if case.get("created_by_id") is not None else None,
        json.dumps(case, separators=(",", ":"), default=str),
    )


def _migrate_legacy_json(conn: sqlite3.Connection, data_dir: Path) -> None:
    def _done(flag: str) -> bool:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (flag,)).fetchone()
        return bool(row and row["value"] == "1")

    def _mark(flag: str) -> None:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (flag, "1"),
        )

    def _run_step(flag: str, filenames: tuple[str, ...], importer) -> None:
        if _done(flag):
            return
        for fname in filenames:
            src = data_dir / fname
            if not src.exists():
                continue
            try:
                raw = json.loads(src.read_text(encoding="utf-8"))
                importer(raw, src)
                # Mark done only after clean parse + import so partial/corrupt files retry.
                _mark(flag)
            except Exception as exc:
                logger.warning("Could not migrate legacy %s (will retry next init): %s", fname, exc)
            return
        # No legacy JSON exists on disk; mark complete.
        _mark(flag)

    def _import_cases(rows, cases_file: Path) -> None:
        if not isinstance(rows, list):
            raise ValueError("cases.json root must be a list")
        for c in rows:
            if isinstance(c, dict) and c.get("id"):
                conn.execute(
                    """INSERT OR IGNORE INTO cases(
                        id, status, agent_id, opened_at, assigned_at, reported_at,
                        closed_at, updated_at, report_msg_id, unit_number,
                        driver_name, group_name, source, workspace_request_id,
                        created_by_id, data_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    _extract_case_columns(c),
                )
        logger.info("Migrated %d legacy case(s) from %s", len(rows), cases_file)

    def _import_users(raw, users_file: Path) -> None:
        if not isinstance(raw, dict):
            raise ValueError("users.json root must be a dict")
        for uid_str, u in raw.items():
            if not isinstance(u, dict):
                continue
            try:
                uid = int(uid_str)
            except (TypeError, ValueError):
                continue
            extra = {k: v for k, v in u.items() if k not in ("name", "username", "role")}
            conn.execute(
                """INSERT OR IGNORE INTO users(id, name, username, role, extra_json, updated_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    uid,
                    str(u.get("name") or ""),
                    str(u.get("username") or "").lstrip("@"),
                    str(u.get("role") or "agent"),
                    json.dumps(extra, separators=(",", ":"), default=str),
                    "",
                ),
            )
        logger.info("Migrated %d legacy user(s) from %s", len(raw), users_file)

    def _import_alerts(raw, _alerts_file: Path) -> None:
        if not isinstance(raw, dict):
            raise ValueError("active_alerts.json root must be a dict")
        for aid, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            tb = rec.get("taken_by")
            tb_id = int(tb[0]) if isinstance(tb, (list, tuple)) and tb else None
            conn.execute(
                """INSERT OR IGNORE INTO active_alerts(alert_id, created_at, taken_by_id, data_json, updated_at)
                   VALUES(?,?,?,?,?)""",
                (
                    str(aid),
                    str(rec.get("created_at") or ""),
                    tb_id,
                    json.dumps(rec, separators=(",", ":"), default=str),
                    "",
                ),
            )
        logger.info("Migrated %d legacy active alert(s)", len(raw))

    def _import_prefs(raw, pfile: Path) -> None:
        if not isinstance(raw, dict):
            raise ValueError(f"{pfile.name} root must be a dict")
        for uid_str, val in raw.items():
            if isinstance(val, dict):
                conn.execute(
                    "INSERT OR IGNORE INTO preferences(user_id, data_json, updated_at) VALUES(?,?,?)",
                    (str(uid_str), json.dumps(val, ensure_ascii=False, separators=(",", ":")), ""),
                )
        logger.info("Migrated preferences from %s", pfile)

    def _import_perms(raw, _perms_file: Path) -> None:
        if not isinstance(raw, dict):
            raise ValueError("role_permissions.json root must be a dict")
        for role, pages in raw.items():
            if isinstance(pages, list):
                conn.execute(
                    "INSERT OR IGNORE INTO role_permissions(role, pages_json) VALUES(?,?)",
                    (str(role), json.dumps(pages)),
                )

    def _import_notifs(raw, _nfile: Path) -> None:
        if not isinstance(raw, list):
            raise ValueError("notifications.json root must be a list")
        for item in raw[-1000:]:
            if isinstance(item, dict) and item.get("id"):
                conn.execute(
                    """INSERT OR IGNORE INTO notifications(id, user_key, created_at, seen, data_json)
                       VALUES(?,?,?,?,?)""",
                    (
                        str(item["id"]),
                        str(item.get("user") or "user"),
                        str(item.get("created_at") or ""),
                        1 if item.get("seen") else 0,
                        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    ),
                )

    def _import_order(raw, _ofile: Path) -> None:
        if isinstance(raw, dict) and isinstance(raw.get("groups"), dict):
            conn.execute(
                "INSERT OR IGNORE INTO kv_state(key, version, data_json) VALUES(?,?,?)",
                (
                    "workspace_card_order",
                    int(raw.get("version") or 0),
                    json.dumps(raw.get("groups") or {}, separators=(",", ":")),
                ),
            )

    _run_step("migrated_cases", ("cases.json",), _import_cases)
    _run_step("migrated_users", ("users.json",), _import_users)
    _run_step("migrated_alerts", ("active_alerts.json",), _import_alerts)
    _run_step("migrated_prefs", ("dashboard_preferences.json", "frontend_preferences.json"), _import_prefs)
    _run_step("migrated_perms", ("role_permissions.json",), _import_perms)
    _run_step("migrated_notifs", ("dashboard_notifications.json", "notifications.json"), _import_notifs)
    _run_step("migrated_order", ("workspace_card_order.json",), _import_order)

    for ver_key in ("cases_version", "users_version", "shifts_version", "feedback_version"):
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES(?, '1')", (ver_key,))


def _migrate_bot_feedback_columns(conn: sqlite3.Connection) -> None:
    """Add private notes and resolution reason to existing volumes, without data loss."""
    existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(bot_feedback)")}
    for name in ("internal_note", "resolution_reason", "diagnostics", "assigned_to", "resolved_at"):
        if name not in existing:
            default = "'{ }'".replace(" ", "") if name == "diagnostics" else "''"
            conn.execute(f"ALTER TABLE bot_feedback ADD COLUMN {name} TEXT NOT NULL DEFAULT {default}")
    conn.execute("""CREATE TABLE IF NOT EXISTS bot_feedback_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feedback_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        author_name TEXT NOT NULL DEFAULT '',
        sender_role TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        media_type TEXT NOT NULL DEFAULT '',
        telegram_file_id TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        mime_type TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(feedback_id) REFERENCES bot_feedback(id) ON DELETE CASCADE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dashboard_release_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_release_notes_recent ON dashboard_release_notes(id DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS feedback_private_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feedback_id TEXT NOT NULL REFERENCES bot_feedback(id) ON DELETE CASCADE,
        author_id TEXT NOT NULL,
        author_name TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        file_key TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        mime_type TEXT NOT NULL DEFAULT '',
        pinned INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        edited_at TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS feedback_note_reactions (
        note_id INTEGER NOT NULL REFERENCES feedback_private_notes(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL,
        vote INTEGER NOT NULL CHECK(vote IN (-1,1)),
        PRIMARY KEY(note_id,user_id)
    )""")
    note_cols={str(row["name"]) for row in conn.execute("PRAGMA table_info(feedback_private_notes)")}
    if "edited_at" not in note_cols:
        conn.execute("ALTER TABLE feedback_private_notes ADD COLUMN edited_at TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_private_notes_thread ON feedback_private_notes(feedback_id,pinned DESC,id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_messages_thread ON bot_feedback_messages(feedback_id, id)")
    # Existing Telegram screenshot IDs remain readable; new website uploads are stored on the persistent volume.
    message_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(bot_feedback_messages)")}
    if "local_file_key" not in message_cols:
        conn.execute("ALTER TABLE bot_feedback_messages ADD COLUMN local_file_key TEXT NOT NULL DEFAULT ''")


def _migrate_live_board_schema(conn: sqlite3.Connection) -> None:
    """Create normalized Live Board tables, fractional ranking columns, and delta event log."""
    case_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(cases)")}
    additions = (
        ("board_stage", "TEXT NOT NULL DEFAULT ''"),
        ("board_rank", "REAL NOT NULL DEFAULT 0"),
        ("priority", "TEXT NOT NULL DEFAULT 'normal'"),
        ("vehicle_type", "TEXT NOT NULL DEFAULT ''"),
        ("issue_text", "TEXT NOT NULL DEFAULT ''"),
        ("shop_info", "TEXT NOT NULL DEFAULT ''"),
        ("amount", "TEXT NOT NULL DEFAULT ''"),
        ("load_type", "TEXT NOT NULL DEFAULT ''"),
        ("pickup", "TEXT NOT NULL DEFAULT ''"),
        ("delivery", "TEXT NOT NULL DEFAULT ''"),
        ("notes_count", "INTEGER NOT NULL DEFAULT 0"),
        ("attachment_count", "INTEGER NOT NULL DEFAULT 0"),
    )
    for col_name, col_def in additions:
        if col_name not in case_cols:
            conn.execute(f"ALTER TABLE cases ADD COLUMN {col_name} {col_def}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_live_board ON cases(status, closed_at, board_stage, agent_id, board_rank ASC, reported_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_unit_search ON cases(unit_number, driver_name, priority)")

    conn.execute("""CREATE TABLE IF NOT EXISTS case_notes (
        id            TEXT PRIMARY KEY,
        case_id       TEXT NOT NULL,
        author_id     TEXT NOT NULL DEFAULT '',
        author_name   TEXT NOT NULL DEFAULT '',
        body          TEXT NOT NULL DEFAULT '',
        reply_to_id   TEXT,
        file_ids_json TEXT NOT NULL DEFAULT '[]',
        mentions_json TEXT NOT NULL DEFAULT '[]',
        pinned        INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL DEFAULT '',
        edited_at     TEXT NOT NULL DEFAULT '',
        deleted_at    TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_case_notes_case ON case_notes(case_id, pinned DESC, created_at ASC)")

    conn.execute("""CREATE TABLE IF NOT EXISTS case_attachments (
        id             TEXT PRIMARY KEY,
        case_id        TEXT NOT NULL,
        name           TEXT NOT NULL DEFAULT '',
        size           INTEGER NOT NULL DEFAULT 0,
        extension      TEXT NOT NULL DEFAULT '',
        category       TEXT NOT NULL DEFAULT 'attachment',
        proof_type     TEXT NOT NULL DEFAULT '',
        description    TEXT NOT NULL DEFAULT '',
        uploaded_by    TEXT NOT NULL DEFAULT '',
        uploaded_by_id TEXT NOT NULL DEFAULT '',
        created_at     TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_case_attachments_case ON case_attachments(case_id, created_at DESC)")

    conn.execute("""CREATE TABLE IF NOT EXISTS case_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id      TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        actor_id     TEXT NOT NULL DEFAULT '',
        actor_name   TEXT NOT NULL DEFAULT '',
        summary_json TEXT NOT NULL DEFAULT '{}',
        created_at   TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_case_events_cursor ON case_events(id ASC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_case_events_case ON case_events(case_id, id DESC)")

    conn.execute("""CREATE TABLE IF NOT EXISTS workspace_presence (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        user_name TEXT NOT NULL,
        action TEXT NOT NULL,
        case_id TEXT NOT NULL DEFAULT '',
        field TEXT NOT NULL DEFAULT '',
        draft TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL,
        expires_at REAL NOT NULL
    )""")
    pres_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(workspace_presence)")}
    if "draft" not in pres_cols:
        conn.execute("ALTER TABLE workspace_presence ADD COLUMN draft TEXT NOT NULL DEFAULT ''")

    done_row = conn.execute("SELECT value FROM meta WHERE key='live_board_normalized_v1'").fetchone()
    if not (done_row and done_row["value"] == "1"):
        # Only normalize active reported cases (typically ~20-50 rows) during startup
        # so 6,000+ historical closed cases never lock SQLite inside BEGIN IMMEDIATE.
        rows = conn.execute(
            "SELECT id, data_json FROM cases WHERE status='reported' AND (closed_at IS NULL OR closed_at='') LIMIT 250"
        ).fetchall()
        for r in rows:
            try:
                c = json.loads(r["data_json"])
                if isinstance(c, dict):
                    c.setdefault("id", r["id"])
                    sync_normalized_case_row(conn, c)
            except Exception:
                continue
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('live_board_normalized_v1','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )


def sync_normalized_case_row(conn: sqlite3.Connection, case: dict) -> None:
    """Synchronize normalized columns and relational child tables from a case record."""
    cid = str(case.get("id") or "")
    if not cid:
        return
    try:
        rank_val = float(case.get("board_rank") or 0.0)
    except (TypeError, ValueError):
        rank_val = 0.0
    notes = [n for n in (case.get("workspace_notes") or []) if isinstance(n, dict)]
    active_notes_count = sum(1 for n in notes if not n.get("deleted_at"))
    ws_attachments = [a for a in (case.get("workspace_attachments") or []) if isinstance(a, dict) and a.get("id")]
    tg_media = case.get("media") if isinstance(case.get("media"), list) else []
    total_attachments = len(ws_attachments) + len(tg_media)

    conn.execute(
        """UPDATE cases SET
            board_stage=?,
            board_rank=?,
            priority=?,
            vehicle_type=?,
            issue_text=?,
            shop_info=?,
            amount=?,
            load_type=?,
            pickup=?,
            delivery=?,
            notes_count=?,
            attachment_count=?
           WHERE id=?""",
        (
            str(case.get("board_stage") or ""),
            rank_val,
            str(case.get("priority") or "normal"),
            str(case.get("vehicle_type") or ""),
            str(case.get("issue_text") or case.get("description") or ""),
            str(case.get("shop_info") or ""),
            str(case.get("amount") or ""),
            str(case.get("load_type") or ""),
            str(case.get("pickup") or ""),
            str(case.get("delivery") or ""),
            active_notes_count,
            total_attachments,
            cid,
        ),
    )

    for idx, n in enumerate(notes):
        nid = str(n.get("id") or f"legacy-{cid}-{idx}")
        conn.execute(
            """INSERT INTO case_notes(
                id, case_id, author_id, author_name, body, reply_to_id,
                file_ids_json, mentions_json, pinned, created_at, edited_at, deleted_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                body=excluded.body,
                file_ids_json=excluded.file_ids_json,
                pinned=excluded.pinned,
                edited_at=excluded.edited_at,
                deleted_at=excluded.deleted_at""",
            (
                nid,
                cid,
                str(n.get("author_id") or ""),
                str(n.get("author") or "Agent"),
                str(n.get("text") or ""),
                str(n["reply_to_id"]) if n.get("reply_to_id") else None,
                json.dumps(n.get("file_ids") or [], separators=(",", ":")),
                json.dumps(n.get("mentions") or [], separators=(",", ":")),
                1 if n.get("pinned") else 0,
                str(n.get("created_at") or ""),
                str(n.get("edited_at") or ""),
                str(n.get("deleted_at") or ""),
            ),
        )

    keep_att_ids = []
    for a in ws_attachments:
        aid = str(a["id"])
        keep_att_ids.append(aid)
        conn.execute(
            """INSERT INTO case_attachments(
                id, case_id, name, size, extension, category, proof_type,
                description, uploaded_by, uploaded_by_id, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                category=excluded.category,
                proof_type=excluded.proof_type,
                description=excluded.description""",
            (
                aid,
                cid,
                str(a.get("name") or ""),
                int(a.get("size") or 0),
                str(a.get("extension") or ""),
                str(a.get("category") or "attachment"),
                str(a.get("proof_type") or ""),
                str(a.get("description") or ""),
                str(a.get("uploaded_by") or ""),
                str(a.get("uploaded_by_id") or ""),
                str(a.get("created_at") or ""),
            ),
        )
    if keep_att_ids:
        placeholders = ",".join("?" for _ in keep_att_ids)
        conn.execute(
            f"DELETE FROM case_attachments WHERE case_id=? AND id NOT IN ({placeholders})",
            [cid, *keep_att_ids],
        )
    else:
        conn.execute("DELETE FROM case_attachments WHERE case_id=?", (cid,))


def append_case_event(
    conn: sqlite3.Connection,
    case_id: str,
    event_type: str,
    actor_id: str = "",
    actor_name: str = "",
    summary: dict | None = None,
    created_at: str = "",
) -> int:
    """Append an immutable event to the delta stream and return the new cursor ID."""
    cur = conn.execute(
        """INSERT INTO case_events(case_id, event_type, actor_id, actor_name, summary_json, created_at)
           VALUES(?,?,?,?,?,?)""",
        (
            str(case_id or ""),
            str(event_type or "updated"),
            str(actor_id or ""),
            str(actor_name or ""),
            json.dumps(summary or {}, separators=(",", ":"), default=str),
            str(created_at or ""),
        ),
    )
    return int(cur.lastrowid or 0)


def get_latest_event_cursor(data_dir: Path | str | None = None) -> int:
    with read_conn(data_dir) as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM case_events").fetchone()
        return int(row["max_id"]) if row else 0


def get_events_since(cursor: int, limit: int = 120, data_dir: Path | str | None = None) -> list[dict]:
    with read_conn(data_dir) as conn:
        rows = conn.execute(
            """SELECT id, case_id, event_type, actor_id, actor_name, summary_json, created_at
               FROM case_events
               WHERE id > ?
               ORDER BY id ASC
               LIMIT ?""",
            (max(0, int(cursor)), max(1, min(int(limit), 500))),
        ).fetchall()
    result = []
    for r in rows:
        try:
            summary = json.loads(r["summary_json"])
        except Exception:
            summary = {}
        result.append({
            "cursor": int(r["id"]),
            "case_id": r["case_id"],
            "event_type": r["event_type"],
            "actor_id": r["actor_id"],
            "actor_name": r["actor_name"],
            "summary": summary,
            "created_at": r["created_at"],
        })
    return result




def _ensure_ready(data_dir: Path | str | None = None) -> tuple[sqlite3.Connection, Path]:
    d = get_data_dir(data_dir)
    db_path = d / "kurtex.sqlite3"
    marker = d / ".kurtex-db-initialized"
    resolved = str(db_path.resolve())
    if not db_path.exists() and marker.exists():
        raise FileNotFoundError(
            f"Previously initialized database {db_path} is missing; refusing to recreate empty storage."
        )
    conn = _open_connection(db_path)
    if resolved not in _INITIALIZED_DBS or not marker.exists():
        with _INIT_LOCK:
            if resolved not in _INITIALIZED_DBS or not marker.exists():
                if db_path.exists() and marker.exists():
                    try:
                        ready_row = conn.execute(
                            "SELECT value FROM meta WHERE key='live_board_normalized_v1'"
                        ).fetchone()
                        if ready_row and ready_row["value"] == "1":
                            _INITIALIZED_DBS.add(resolved)
                            return conn, d
                    except sqlite3.Error:
                        pass
                for attempt in range(8):
                    try:
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.execute("BEGIN IMMEDIATE")
                        try:
                            for stmt in _SCHEMA_SQL.split(";"):
                                sql = stmt.strip()
                                if sql:
                                    conn.execute(sql)
                            _migrate_bot_feedback_columns(conn)
                            _migrate_legacy_json(conn, d)
                            _migrate_live_board_schema(conn)
                            conn.execute("COMMIT")
                        except BaseException:
                            try:
                                conn.execute("ROLLBACK")
                            except sqlite3.Error:
                                pass
                            raise
                        if not marker.exists():
                            try:
                                marker.touch(mode=0o600)
                            except OSError:
                                marker.touch()
                        _INITIALIZED_DBS.add(resolved)
                        break
                    except sqlite3.OperationalError as exc:
                        if "locked" in str(exc).lower() and attempt < 7:
                            time.sleep(0.05 * (attempt + 1))
                            continue
                        conn.close()
                        raise
    return conn, d


@contextmanager
def read_conn(data_dir: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    conn, _ = _ensure_ready(data_dir)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_tx(data_dir: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    conn, _ = _ensure_ready(data_dir)
    try:
        for attempt in range(8):
            try:
                conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < 7:
                    time.sleep(0.02 * (attempt + 1))
                    continue
                raise
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        conn.close()


def bump_version(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    next_val = (int(row["value"]) if row else 0) + 1
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(next_val)),
    )
    return next_val


def get_version(key: str, data_dir: Path | str | None = None) -> int:
    with read_conn(data_dir) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return int(row["value"]) if row else 0


def reset_init_cache() -> None:
    with _INIT_LOCK:
        _INITIALIZED_DBS.clear()
    close_all_connections()
