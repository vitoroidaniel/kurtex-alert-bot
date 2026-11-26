import tempfile
import unittest
import uuid
from backend.storage import db, board_presence

class PresenceVersionTests(unittest.TestCase):
    def test_presence_version_shared_with_readers(self):
        with tempfile.TemporaryDirectory() as root:
            sid=str(uuid.uuid4())
            before=db.get_version("workspace_presence_version",root)
            board_presence.heartbeat(sid,11,"Agent", "dragging","case-a",data_dir=root)
            self.assertGreater(db.get_version("workspace_presence_version",root),before)
            self.assertEqual(board_presence.snapshot(12,root)["peers"][0]["action"],"dragging")
            version=db.get_version("workspace_presence_version",root)
            board_presence.remove_session(sid,root,user_id=11)
            self.assertGreater(db.get_version("workspace_presence_version",root),version)
            self.assertEqual(board_presence.snapshot(12,root)["peers"],[])
