"""Create and verify standalone SQLite snapshots. Never overwrite a database.

Run with explicitly selected paths. Store backups outside the source volume.
The SQLite backup API includes committed WAL changes without copying sidecars.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)


def integrity(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RuntimeError("SQLite integrity check failed")


def verify_snapshot(path: Path, expected_sha256: str | None = None) -> dict:
    path = path.resolve(strict=True)
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise RuntimeError("Snapshot has SQLite sidecar files; verify a closed standalone copy")
    checksum = digest(path)
    if expected_sha256 is not None and not hmac.compare_digest(checksum, expected_sha256.lower()):
        raise RuntimeError("Snapshot checksum mismatch")
    with closing(read_only(path)) as conn:
        integrity(conn)
    if digest(path) != checksum:
        raise RuntimeError("Snapshot changed during verification")
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": checksum,
            "integrity": "ok"}


def create_snapshot(source: Path, destination: Path, timeout: float = 120) -> dict:
    if not 0 < timeout <= 3600:
        raise ValueError("Timeout must be between 0 and 3600 seconds")
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if not destination.parent.is_dir():
        raise FileNotFoundError(destination.parent)
    fd, temp_name = tempfile.mkstemp(prefix=".sqlite-snapshot-", suffix=".tmp",
                                     dir=destination.parent)
    os.close(fd)
    tmp = Path(temp_name)
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError("SQLite backup deadline exceeded")

    try:
        with closing(read_only(source)) as src, closing(sqlite3.connect(tmp)) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
            dst.execute("PRAGMA journal_mode=DELETE")
            integrity(dst)
        report = verify_snapshot(tmp)
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        # Publish atomically and fail if any other process created destination.
        # A hard link avoids os.replace() overwriting an existing database.
        os.link(tmp, destination)
        if hasattr(os, "O_DIRECTORY"):
            dir_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        report["file"] = destination.name
        report["created_utc"] = datetime.now(timezone.utc).isoformat()
        return report
    finally:
        tmp.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(tmp) + suffix).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Snapshot one SQLite database to a new file")
    create.add_argument("source", type=Path)
    create.add_argument("destination", type=Path)
    create.add_argument("--timeout", type=float, default=120)
    verify = commands.add_parser("verify", help="Verify a closed, standalone snapshot")
    verify.add_argument("snapshot", type=Path)
    verify.add_argument("--sha256")
    args = parser.parse_args()
    try:
        result = (create_snapshot(args.source, args.destination, args.timeout)
                  if args.command == "create"
                  else verify_snapshot(args.snapshot, args.sha256))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
