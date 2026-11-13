"""Developer note editing respects ownership and preserves pinned/reaction state."""
import os
import tempfile
import unittest
from unittest.mock import patch
from backend.storage import db, feedback_store

class PrivateNotesEditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ, {"DATA_DIR":self.temp.name})
        self.env.start()
        db.reset_init_cache()
        self.report=feedback_store.create_feedback(user_id=13,user_name="Reporter",
            username="reporter",user_role="agent",category="bug",message="Broken modal")

    def tearDown(self):
        db.reset_init_cache()
        self.env.stop()
        self.temp.cleanup()

    def test_author_can_edit_only_own_note(self):
        note=feedback_store.create_private_note(self.report["id"],author_id="91",
            author_name="Developer",body="Initial debugging context")
        feedback_store.change_private_note(self.report["id"],note["id"],user_id="91",
            body="Updated context")
        record=feedback_store.list_private_notes(self.report["id"],"91")[0]
        self.assertEqual(record["body"],"Updated context")
        self.assertTrue(record["edited_at"])
        self.assertEqual(record["author_id"],"91")
        with self.assertRaises(PermissionError):
            feedback_store.change_private_note(self.report["id"],note["id"],user_id="92",
                body="Unauthorized rewrite")
        self.assertEqual(feedback_store.list_private_notes(self.report["id"],"92")[0]["body"],"Updated context")

    def test_edit_validation_and_reactions(self):
        note=feedback_store.create_private_note(self.report["id"],author_id="91",
            author_name="Developer",body="Please investigate")
        with self.assertRaises(ValueError):
            feedback_store.change_private_note(self.report["id"],note["id"],user_id="91",body="  ")
        feedback_store.change_private_note(self.report["id"],note["id"],user_id="92",
            pinned=True,vote=1)
        record=feedback_store.list_private_notes(self.report["id"],"92")[0]
        self.assertEqual(record["pinned"],1)
        self.assertEqual(record["likes"],1)

if __name__=="__main__":
    unittest.main()
