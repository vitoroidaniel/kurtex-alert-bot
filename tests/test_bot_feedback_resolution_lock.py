"""Persistent private notes and permanent resolution lock for bot feedback."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.storage import db, feedback_store


class FeedbackResolutionLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch_data = patch.dict(os.environ, {"DATA_DIR": self.temp.name})
        self.patch_data.start()
        db.reset_init_cache()

    def tearDown(self):
        db.reset_init_cache()
        self.patch_data.stop()
        self.temp.cleanup()

    def make_report(self):
        return feedback_store.create_feedback(
            user_id=22, user_name="Agent", username="agent",
            user_role="agent", category="bug", message="Bot command failed",
        )

    def test_private_notes_not_mixed_with_public_message(self):
        item = self.make_report()
        updated = feedback_store.update_feedback(
            item["id"], status="in_progress", admin_note="We are investigating",
            internal_note="Secret internal debugging context", resolved_by="Manager"
        )
        self.assertEqual(updated["admin_note"], "We are investigating")
        self.assertEqual(updated["internal_note"], "Secret internal debugging context")
        self.assertEqual(updated["status"], "in_progress")

    def test_reason_required_and_lock_persists(self):
        item = self.make_report()
        with self.assertRaisesRegex(ValueError, "resolution reason"):
            feedback_store.update_feedback(item["id"], status="resolved")
        done = feedback_store.update_feedback(
            item["id"], status="resolved", resolution_reason="Fixed an invalid parser",
            admin_note="The issue is resolved.", internal_note="Rebuilt parser", resolved_by="Manager"
        )
        self.assertEqual(done["resolution_reason"], "Fixed an invalid parser")
        self.assertEqual(done["resolved_by"], "Manager")
        with self.assertRaisesRegex(ValueError, "read-only"):
            feedback_store.update_feedback(item["id"], admin_note="Changed afterwards")
        with self.assertRaisesRegex(ValueError, "locked"):
            feedback_store.delete_feedback(item["id"])
        reopened = feedback_store.update_feedback(item["id"], status="open", resolved_by="Manager")
        self.assertEqual(reopened["status"], "open")
        self.assertEqual(reopened["resolved_by"], "")
        self.assertEqual(reopened["resolution_reason"], "Fixed an invalid parser")

    def test_schema_migrates_existing_feedback_rows(self):
        db_path = Path(self.temp.name) / "kurtex.sqlite3"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""CREATE TABLE bot_feedback(
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL DEFAULT '', username TEXT NOT NULL DEFAULT '',
                user_role TEXT NOT NULL DEFAULT 'agent',category TEXT NOT NULL DEFAULT 'bug',
                message TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',
                admin_note TEXT NOT NULL DEFAULT '',resolved_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
            conn.execute("""INSERT INTO bot_feedback(id,user_id,message,created_at,updated_at)
                            VALUES ('old-1',1,'Old issue','2026-11-11','2026-11-11')""")
        db.reset_init_cache()
        legacy = feedback_store.get_feedback("old-1")
        self.assertEqual(legacy["message"], "Old issue")
        self.assertEqual(legacy["internal_note"], "")
        self.assertEqual(legacy["resolution_reason"], "")


if __name__ == "__main__":
    unittest.main()
