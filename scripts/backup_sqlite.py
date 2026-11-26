"""Create verified online SQLite snapshots; never overwrite a source database."""
import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or source == destination or destination.exists():
        raise ValueError("Source must exist and destination must be new")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".backup-", dir=destination.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)) as src, closing(sqlite3.connect(tmp)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Backup integrity check failed")
            if dst.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Backup has foreign key violations")
        os.link(tmp, destination)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return destination

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    print(snapshot(args.source, args.destination))
