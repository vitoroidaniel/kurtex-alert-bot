import tempfile
import sqlite3
import unittest
from pathlib import Path
from contextlib import closing
from scripts.backup_sqlite import snapshot
from backend.ai.ai_chat_store import ChatStore
from backend.storage import board_presence

class HardeningTests(unittest.TestCase):
    def test_backup_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"source.db"
            dest=Path(folder)/"backup.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE records (value TEXT)")
                db.execute("INSERT INTO records VALUES ('sample')")
                db.commit()
            snapshot(source,dest)
            with closing(sqlite3.connect(dest)) as db:
                self.assertEqual(db.execute("SELECT value FROM records").fetchone(),("sample",))
            with self.assertRaises(ValueError):
                snapshot(source,dest)

    def test_chat_blob_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ChatStore(folder)
            key=store.put_blob(b"example")
            self.assertEqual(store.blob_path(key).read_bytes(),b"example")
            self.assertEqual(store.put_blob(b"example"),key)
            store.save("one",[{"id":"chat","attachments":[]}])
            self.assertEqual(store.read("one")[0]["id"],"chat")
            self.assertEqual(store.read("two"),[])

    def test_presence_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            sid="12345678-1234-4234-8234-123456789abc"
            board_presence.heartbeat(sid,1,"One",data_dir=folder)
            board_presence.heartbeat(sid,2,"Two",data_dir=folder)
            board_presence.heartbeat(sid,2,"Two",action="left",data_dir=folder)
            members=board_presence.snapshot(1,data_dir=folder)["members"]
            self.assertEqual([m["user_id"] for m in members],[1])

    def test_concurrent_transaction_versions(self):
        from concurrent.futures import ThreadPoolExecutor
        from backend.storage import db
        with tempfile.TemporaryDirectory() as folder:
            with db.write_tx(folder) as conn:
                db.bump_version(conn, "hardening_test")
            def update(_):
                with db.write_tx(folder) as conn:
                    return db.bump_version(conn, "hardening_test")
            with ThreadPoolExecutor(max_workers=30) as workers:
                versions = list(workers.map(update, range(60)))
            self.assertEqual(sorted(versions), list(range(2, 62)))
            self.assertEqual(db.get_version("hardening_test", folder), 61)

    def test_transaction_rollback(self):
        from backend.storage import db
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                with db.write_tx(folder) as conn:
                    db.bump_version(conn, "rollback_test")
                    raise RuntimeError("simulated failure")
            self.assertEqual(db.get_version("rollback_test", folder), 0)
