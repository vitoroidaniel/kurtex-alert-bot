"""Load/consistency checks for concurrent API and Telegram case writes."""
import json
import multiprocessing as mp
import os
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.storage import case_store


def writer(path, worker_id, count, start):
    from backend.storage import case_store as store
    store.CASES_FILE = Path(path)
    for index in range(count):
        store.create_case(f"stress-{worker_id}-{index}", "Driver", None, "#repairs", "Issue")
    return time.monotonic() - start


class ConcurrentCasesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'cases.json'
        self.old = case_store.CASES_FILE
        case_store.CASES_FILE = self.path
        self.history = [{"id":f"history-{i}","status":"done", "unit_number":f"R{i}",
                         "opened_at":"2026-10-12T09:00:00+00:00", "closed_at":"2026-10-12T10:00:00+00:00",
                         "description":"historical issue", "agent_name":"Alex"} for i in range(2000)]
        case_store._save(self.path, self.history, strict=True)

    def tearDown(self):
        case_store.CASES_FILE = self.old
        self.tmp.cleanup()

    def test_ten_processes_cannot_lose_updates(self):
        workers = 10
        start = time.monotonic()
        ctx = mp.get_context('fork')
        jobs = [ctx.Process(target=writer, args=(str(self.path), n, 12, start)) for n in range(workers)]
        for job in jobs: job.start()
        for job in jobs:
            job.join(45)
            self.assertFalse(job.is_alive(), 'Writer timed out')
            self.assertEqual(job.exitcode, 0)
        rows = json.loads(self.path.read_text())
        self.assertEqual(len(rows), 2000 + workers * 12)
        self.assertEqual(len({row['id'] for row in rows}), len(rows))
        print(f'\n10-process/120-write/2000-history-case test: {time.monotonic()-start:.3f}s; {self.path.stat().st_size/1024:.0f} KiB')

    def test_same_case_stale_edit_is_rejected(self):
        actor = {"id":42, "role":"agent", "name":"Alex"}
        payload = {"id":"live-1", "unit_number":"R193", "driver":"Driver", "issue":"Air leak",
                   "vehicle_type":"truck", "priority":"medium", "request_id":"request-1",
                   "target_agent":{"id":42,"name":"Alex","username":"alex"}}
        created = case_store.workspace_write(actor, payload)
        original_revision = case_store.workspace_revision(created)
        first = case_store.workspace_write(actor, {"action":"edit", "revision":original_revision,
                                                  "fields":{"issue_text":"Updated issue"}}, created['id'])
        self.assertEqual(first['issue_text'], 'Updated issue')
        with self.assertRaises(ValueError):
            case_store.workspace_write(actor, {"action":"edit", "revision":original_revision,
                                         "fields":{"issue_text":"Stale overwrite"}}, created['id'])
        self.assertEqual(case_store.get_case(created['id'])['issue_text'], 'Updated issue')

    def test_missing_storage_does_not_erase_history(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            case_store.create_case('unsafe', 'Driver', None, '#repairs', 'Issue')
        self.assertFalse(self.path.exists())

    def test_endpoint_contract_present(self):
        source = (Path(__file__).resolve().parents[1] / 'api/server.py').read_text()
        self.assertIn('@app.route("/api/workspace/board")', source)
        self.assertIn('summary["revision"] = workspace_revision(c)', source)
        self.assertIn('"unchanged": True', source)
        self.assertIn('"can_manage"] = _workspace_can_manage(c, actor)', source)

    @unittest.skipUnless(importlib.util.find_spec('flask'), 'Flask unavailable in offline test environment')
    def test_board_version_and_revision(self):
        from api import server
        case = {"id":"live-1", "status":"reported", "reported_at":"2026-10-13T10:00:00+00:00",
                "unit_number":"R193", "issue_text":"Air pressure low", "report_driver":"Driver",
                "agent_id":42, "agent_name":"Alex", "opened_at":"2026-10-13T09:00:00+00:00"}
        case_store._save(self.path, self.history + [case], strict=True)
        actor = {"id":42,"role":"agent", "name":"Alex"}
        with patch.object(server, 'DATA_DIR', Path(self.tmp.name)), patch.object(server, '_workspace_actor_read', return_value=(actor, None)):
            server.case_snapshot.signature = None
            with server.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user'] = actor
                first = client.get('/api/workspace/board')
                self.assertEqual(first.status_code, 200)
                data = first.get_json()
                self.assertEqual(data['total'], 1)
                self.assertEqual(data['cases'][0]['full_id'], 'live-1')
                self.assertTrue(data['cases'][0]['can_manage'])
                self.assertEqual(data['cases'][0]['revision'], case_store.workspace_revision(case))
                version = data['version']
                unchanged = client.get('/api/workspace/board?since='+version)
                self.assertEqual(unchanged.status_code, 200)
                self.assertTrue(unchanged.get_json()['unchanged'])
                new_case = dict(case, id='live-2', unit_number='R194')
                case_store._save(self.path, self.history + [case, new_case], strict=True)
                updated = client.get('/api/workspace/board?since='+version)
                self.assertFalse(updated.get_json()['unchanged'])
                self.assertEqual(updated.get_json()['total'], 2)

if __name__=='__main__': unittest.main()
