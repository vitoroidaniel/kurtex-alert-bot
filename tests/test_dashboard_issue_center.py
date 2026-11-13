"""Dashboard ticket storage: own reports, local attachments and locked conversations."""
import os
import tempfile
import unittest
from unittest.mock import patch
from backend.storage import db,feedback_store


class IssueStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{"DATA_DIR":self.tmp.name})
        self.env.start()
        db.reset_init_cache()
        self.issue=feedback_store.create_feedback(
            user_id=123,user_name="Agent",username="agent",user_role="agent",
            category="bug",message="Case history search broken")
    def tearDown(self):
        db.reset_init_cache()
        self.env.stop()
        self.tmp.cleanup()

    def test_reporter_sees_own_tickets_not_developer_notes(self):
        mine=feedback_store.list_my_feedback(123)
        self.assertEqual(len(mine),1)
        self.assertEqual(mine[0]['user_id'],123)
        self.assertNotIn('internal_note',mine[0])
        self.assertEqual(feedback_store.list_my_feedback(999),[])
        self.assertIsNone(feedback_store.get_feedback_by_user(self.issue['id'],999))

    def test_persistent_media_metadata_without_secret_reference_in_timeline(self):
        fid=self.issue['id']
        key="a"*40+".png"
        msg=feedback_store.add_feedback_message(
            fid,author_id="123",author_name="Agent",sender_role="reporter",
            message="Look at the screenshot",media_type="photo",
            mime_type="image/png",file_name="error.png",local_file_key=key)
        rows=feedback_store.list_feedback_messages(fid)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['file_name'],"error.png")
        self.assertNotIn('local_file_key',rows[0])
        self.assertNotIn('telegram_file_id',rows[0])
        self.assertEqual(feedback_store.get_feedback_media(fid,msg['id'])['local_file_key'],key)
        with self.assertRaises(PermissionError):
            feedback_store.add_feedback_message(
                fid,author_id="999",author_name="Outsider",sender_role="reporter",message="Hi")
        feedback_store.update_feedback(fid,status="resolved",resolution_reason="Fixed")
        with self.assertRaisesRegex(ValueError,"read-only"):
            feedback_store.add_feedback_message(fid,author_id="123",author_name="Agent",sender_role="reporter",message="New reply")

    def test_in_app_notifications(self):
        feedback_store.notify_feedback_users([123],title="Reply received",
            message="A developer replied.",feedback_id=self.issue['id'],kind="issue_reply")
        with db.read_conn() as conn:
            row=conn.execute("SELECT user_key,data_json FROM notifications WHERE user_key='123'").fetchone()
        self.assertEqual(row['user_key'],'123')
        self.assertIn("Reply received",row['data_json'])


if __name__=='__main__':
    unittest.main()
