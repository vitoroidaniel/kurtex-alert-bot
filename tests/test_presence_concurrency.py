import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from backend.storage import db
from backend.storage.board_presence import heartbeat, snapshot

class PresenceConcurrencyTests(unittest.TestCase):
    def test_twenty_agents_and_owned_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with db.write_tx(root):
                pass
            sessions = [str(uuid.uuid4()) for _ in range(20)]
            barrier = Barrier(20)
            def worker(i):
                barrier.wait(timeout=10)
                for _ in range(5):
                    heartbeat(sessions[i], i+1, f'Fixture {i}', 'viewing', 'case-fixture', data_dir=root)
                return snapshot(i+1, root)
            with ThreadPoolExecutor(max_workers=20) as pool:
                list(pool.map(worker, range(20)))
            self.assertEqual(snapshot(1, root)['online'], 20)
            with self.assertRaises(ValueError):
                heartbeat(sessions[0], 2, 'Wrong owner', data_dir=root)
            heartbeat(sessions[0], 2, 'Wrong owner', 'left', data_dir=root)
            self.assertEqual(snapshot(1, root)['online'], 20)
            heartbeat(sessions[0], 1, 'Fixture 0', 'left', data_dir=root)
            self.assertEqual(snapshot(1, root)['online'], 19)
            self.assertFalse(any(x['user_id']==1 for x in snapshot(2, root)['members']))
