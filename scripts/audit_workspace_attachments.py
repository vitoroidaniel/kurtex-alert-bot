"""Read-only workspace attachment inventory; never deletes or repairs files.

Run on a verified SQLite snapshot and a consistent copy of workspace_uploads.
Live uploads can temporarily look unreferenced; do not use this report for deletion.
Only aggregate counts are printed, not filenames or case identities.
"""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from scripts.sqlite_snapshot import read_only


def audit(database: Path, uploads: Path) -> dict:
    expected = set()
    invalid_metadata = 0
    def add(case_id, item, fallback=""):
        nonlocal invalid_metadata
        if not isinstance(item, dict):
            invalid_metadata += 1
            return
        blob = str(item.get("blob_id") or item.get("id") or fallback)
        extension = str(item.get("extension") or "")
        if (not blob or not extension.startswith(".") or
            any(not x or x in (".", "..") or "/" in x or "\\" in x
                for x in (case_id, blob, extension))):
            invalid_metadata += 1
            return
        expected.add(case_id + "/" + blob + extension)

    with closing(read_only(database)) as connection:
        for case_id, raw in connection.execute("SELECT id, data_json FROM cases"):
            try:
                record = json.loads(raw)
                if not isinstance(record, dict):
                    raise ValueError()
                attachments = record.get("workspace_attachments") or []
                if not isinstance(attachments, list):
                    raise ValueError()
            except (ValueError, TypeError):
                invalid_metadata += 1
                continue
            for item in attachments:
                add(str(case_id), item)
                if isinstance(item, dict):
                    versions = item.get("versions") or []
                    if isinstance(versions, list):
                        for version in versions:
                            add(str(case_id), version)
                    else:
                        invalid_metadata += 1
    actual = set()
    symlinks = 0
    total_bytes = 0
    if not uploads.is_dir() or uploads.is_symlink():
        raise ValueError("Expected a real workspace_uploads directory")
    for directory in uploads.iterdir():
        if directory.is_symlink():
            symlinks += 1
            continue
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_symlink():
                symlinks += 1
            elif path.is_file():
                actual.add(directory.name + "/" + path.name)
                total_bytes += path.stat().st_size
    return {"referenced_blobs": len(expected), "files": len(actual),
            "missing_blobs": len(expected - actual),
            "unreferenced_candidates": len(actual - expected),
            "invalid_metadata": invalid_metadata, "skipped_symlinks": symlinks,
            "total_bytes": total_bytes,
            "mode": "read-only; candidates require manual verification"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("uploads", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.database, args.uploads), sort_keys=True))
