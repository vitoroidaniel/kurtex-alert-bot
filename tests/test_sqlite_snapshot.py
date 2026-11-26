import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.sqlite_snapshot import create_snapshot, verify_snapshot


class SnapshotTests(unittest.TestCase):
    def test_wal_snapshot_and_restore_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.sqlite3"
            backup = root / "backup.sqlite3"
            restore = root / "restore.sqlite3"
            with sqlite3.connect(source) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA wal_autocheckpoint=0")
                conn.execute("CREATE TABLE fixture(id INTEGER PRIMARY KEY, note TEXT)")
                conn.executemany("INSERT INTO fixture(note) VALUES (?)",
                                 (("synthetic",) for _ in range(50000)))
                conn.commit()
                report = create_snapshot(source, backup)
                self.assertTrue(Path(str(source) + "-wal").exists())
                conn.execute("INSERT INTO fixture(note) VALUES ('later')")
                conn.commit()
                self.assertEqual(report["integrity"], "ok")
                self.assertEqual(verify_snapshot(backup, report["sha256"])["sha256"],
                                 report["sha256"])
                create_snapshot(backup, restore)
                with sqlite3.connect(restore) as restored:
                    self.assertEqual(restored.execute("SELECT COUNT(*) FROM fixture").fetchone(),
                                     (50000,))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM fixture").fetchone(),
                                 (50001,))
            self.assertFalse(Path(str(backup) + "-wal").exists())

    def test_refuses_overwrite_and_bad_checksum(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, backup = root / "source.db", root / "backup.db"
            with sqlite3.connect(source) as conn:
                conn.execute("CREATE TABLE fixture(id INTEGER)")
            report = create_snapshot(source, backup)
            with self.assertRaises(FileExistsError):
                create_snapshot(source, backup)
            with self.assertRaises(RuntimeError):
                verify_snapshot(backup, "0" * 64)
            self.assertEqual(verify_snapshot(backup)["sha256"], report["sha256"])

    def test_missing_and_corrupt_source_leave_no_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "backup.db"
            with self.assertRaises(FileNotFoundError):
                create_snapshot(root / "missing.db", target)
            bad = root / "corrupt.db"
            bad.write_bytes(b"not a sqlite database")
            with self.assertRaises(sqlite3.Error):
                create_snapshot(bad, target)
            self.assertFalse(target.exists())
            self.assertFalse(list(root.glob(".sqlite-snapshot-*")))


if __name__ == "__main__":
    unittest.main()
