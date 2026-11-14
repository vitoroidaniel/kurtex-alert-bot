"""Regression checks for Live Board ordering and expiring team presence."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from backend.storage import db
from backend.storage.board_order import OrderConflict, read_order, save_order
from backend.storage.board_presence import heartbeat, snapshot


class LiveBoardCollaborationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        with db.write_tx(self.data_dir) as conn:
            for case_id in ('case-a', 'case-b', 'case-c'):
                conn.execute(
                    "INSERT INTO cases(id,status,agent_id,data_json) VALUES(?,?,?,?)",
                    (case_id, 'reported', 11, json.dumps({'board_stage':''}))
                )

    def tearDown(self):
        self.tmp.cleanup()

    def test_order_is_saved_and_rejects_stale_writes(self):
        value = save_order('agent_workflow:agent:11', ['case-b','case-a','case-c'], 0,
                           11, manager=False, data_dir=self.data_dir)
        self.assertEqual(value['version'], 1)
        self.assertEqual(read_order(self.data_dir)['groups']['agent_workflow:agent:11'],
                         ['case-b','case-a','case-c'])
        with self.assertRaises(OrderConflict):
            save_order('agent_workflow:agent:11', ['case-a','case-b'], 0, 11,
                       manager=False, data_dir=self.data_dir)

    def test_foreign_lane_and_duplicate_ids_rejected(self):
        with self.assertRaises(PermissionError):
            save_order('agent_workflow:agent:12', ['case-a'], 0, 11,
                       manager=False, data_dir=self.data_dir)
        with self.assertRaises(ValueError):
            save_order('agent_workflow:agent:11', ['case-a','case-a'], 0, 11,
                       manager=False, data_dir=self.data_dir)

    def test_editing_presence_shows_real_user_names_and_expires(self):
        heartbeat(str(uuid.uuid4()), 11, 'Daniel', 'editing', 'case-a', 'issue', self.data_dir)
        heartbeat(str(uuid.uuid4()), 12, 'Andrei', 'viewing', 'case-a', '', self.data_dir)
        result = snapshot(11, self.data_dir)
        self.assertEqual(result['online'], 2)
        self.assertEqual(result['peers'][0]['name'], 'Andrei')
        with db.write_tx(self.data_dir) as conn:
            conn.execute("UPDATE workspace_presence SET expires_at=0 WHERE user_id=12")
        self.assertEqual(snapshot(11, self.data_dir)['online'], 1)


if __name__ == '__main__':
    unittest.main()
