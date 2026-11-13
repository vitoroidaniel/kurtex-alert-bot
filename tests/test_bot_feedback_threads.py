"""Bot Report threads: persistence, ownership and immutable resolutions."""
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.storage import db, feedback_store


class FeedbackThreadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DATA_DIR": self.tmp.name})
        self.env.start()
        db.reset_init_cache()
        self.report = feedback_store.create_feedback(
            user_id=101, user_name="Reporter", username="reporter",
            user_role="agent", category="bug", message="The menu is broken"
        )

    def tearDown(self):
        db.reset_init_cache()
        self.env.stop()
        self.tmp.cleanup()

    def test_reporter_only_sees_own_reports(self):
        self.assertEqual(len(feedback_store.list_my_feedback(101)), 1)
        self.assertEqual(feedback_store.list_my_feedback(202), [])
        mine = feedback_store.list_my_feedback(101)[0]
        self.assertNotIn("internal_note", mine)

    def test_bidirectional_messages_and_photo_reference(self):
        fid = self.report["id"]
        reporter = feedback_store.add_feedback_message(
            fid, author_id="101", author_name="Reporter", sender_role="reporter",
            message="See screenshot", media_type="photo", telegram_file_id="tg-test-file",
            file_name="screen.jpg", mime_type="image/jpeg"
        )
        staff = feedback_store.add_feedback_message(
            fid, author_id="7", author_name="Manager", sender_role="manager",
            message="We are investigating."
        )
        messages = feedback_store.list_feedback_messages(fid)
        self.assertEqual([m["sender_role"] for m in messages], ["reporter", "manager"])
        self.assertEqual(messages[0]["file_name"], "screen.jpg")
        self.assertNotIn("telegram_file_id", messages[0])  # Never return internal bot IDs.
        self.assertEqual(feedback_store.get_feedback_media(fid, reporter["id"])["telegram_file_id"], "tg-test-file")
        self.assertIsNone(feedback_store.get_feedback_media(fid, staff["id"]))

    def test_wrong_user_and_locked_reports_rejected(self):
        fid = self.report["id"]
        with self.assertRaises(PermissionError):
            feedback_store.add_feedback_message(
                fid, author_id="999", author_name="Intruder", sender_role="reporter",
                message="Should be denied"
            )
        feedback_store.update_feedback(fid, status="resolved", resolution_reason="Fixed", resolved_by="Manager")
        with self.assertRaisesRegex(ValueError, "read-only"):
            feedback_store.add_feedback_message(
                fid, author_id="101", author_name="Reporter",
                sender_role="reporter", message="Too late"
            )
        with self.assertRaisesRegex(ValueError, "read-only"):
            feedback_store.add_feedback_message(
                fid, author_id="7", author_name="Manager", sender_role="manager",
                message="Cannot change resolved case"
            )

    def test_empty_messages_rejected(self):
        with self.assertRaisesRegex(ValueError, "required"):
            feedback_store.add_feedback_message(
                self.report["id"], author_id="101", author_name="Reporter",
                sender_role="reporter", message=""
            )


if __name__ == "__main__":
    unittest.main()
