"""Tests for Global Activity History endpoint and filtering."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from api import server
from api.routes import workspace_board
from backend.ai.fleet_knowledge import FleetKnowledgeStore
from backend.storage import case_store, db, preference_store, user_store


class GlobalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        for target, attr in (
            (case_store, "_storage_dir"),
            (preference_store, "_storage_dir"),
            (user_store, "_storage_dir"),
        ):
            self.enterContext(patch.object(target, attr, return_value=self.data_dir))
        self.enterContext(patch.object(server, "DATA_DIR", self.data_dir))
        self.knowledge = FleetKnowledgeStore(self.data_dir / "fleet_knowledge.sqlite3")
        self.enterContext(patch.object(server, "fleet_knowledge_store", self.knowledge))

        self.dev = {"id": 100, "name": "Dev", "role": "developer", "username": "dev"}
        self.manager = {"id": 101, "name": "Daniel", "role": "super_admin", "username": "danielv"}
        self.agent_a = {"id": 102, "name": "Alex", "role": "agent", "username": "alexr"}
        self.agent_b = {"id": 103, "name": "Victor", "role": "agent", "username": "victorr"}
        with db.write_tx(self.data_dir) as conn:
            for person in (self.dev, self.manager, self.agent_a, self.agent_b):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (person["id"], person["name"], person["username"], person["role"]),
                )

        self.client = server.app.test_client()

    def _auth(self, user):
        with self.client.session_transaction() as sess:
            sess["user"] = {"id": user["id"], "name": user["name"], "role": user["role"]}
            sess["workspace_csrf"] = "test-csrf"

    def test_permissions_and_filtering_for_global_history(self):
        c1 = case_store.workspace_write(
            self.manager,
            {
                "id": "CASE-H1",
                "request_id": "req-h1",
                "driver": "Sam Driver",
                "unit_number": "391",
                "issue": "Alternator failure",
                "vehicle_type": "truck",
                "priority": "high",
                "target_agent": self.agent_a,
            },
        )
        c1 = case_store.workspace_write(
            self.agent_a,
            {
                "action": "edit",
                "revision": case_store.workspace_revision(c1),
                "fields": {"unit_number": "R391", "shop_info": "Love's Bay 3"},
            },
            "CASE-H1",
        )
        c1 = case_store.workspace_write(
            self.manager,
            {
                "action": "stage",
                "revision": case_store.workspace_revision(c1),
                "stage": "dealer_shop",
            },
            "CASE-H1",
        )
        c_test = case_store.workspace_write(
            self.dev,
            {
                "id": "CASE-TEST",
                "request_id": "req-test",
                "driver": "Tester",
                "unit_number": "999",
                "issue": "Test smoke check",
                "vehicle_type": "truck",
                "priority": "low",
                "target_agent": self.agent_a,
            },
        )
        case_store.workspace_write(self.dev, {"action": "set_test", "is_test": True}, c_test["id"])

        # Permanent deletion of another case records deleted marker
        c_del = case_store.workspace_write(
            self.manager,
            {
                "id": "CASE-DEL",
                "request_id": "req-del",
                "driver": "Drop Driver",
                "unit_number": "555",
                "issue": "Delete me",
                "vehicle_type": "truck",
                "priority": "low",
                "target_agent": self.agent_a,
            },
        )
        case_store.remove_live_board_case(
            self.manager,
            c_del["id"],
            mode="permanent",
            revision=case_store.workspace_revision(c_del),
        )

        # Agent is forbidden
        self._auth(self.agent_a)
        self.assertEqual(self.client.get("/api/workspace/history").status_code, 403)

        # Manager gets non-test history events + permanent delete marker
        self._auth(self.manager)
        res = self.client.get("/api/workspace/history")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["ok"])
        case_ids = {e["case_id"] for e in body["events"]}
        self.assertIn("CASE-H1", case_ids)
        self.assertIn("CASE-DEL", case_ids)
        self.assertNotIn("CASE-TEST", case_ids)

        # Filter by specific agent (Alex -> ID 102)
        res_alex = self.client.get("/api/workspace/history?actor=102")
        alex_events = res_alex.get_json()["events"]
        self.assertEqual(len(alex_events), 1)
        self.assertEqual(alex_events[0]["action"], "edit")
        self.assertEqual(alex_events[0]["unit_number"], "R391")
        self.assertTrue(any(ch["field"] == "unit_number" and ch["to"] == "R391" for ch in alex_events[0]["changes"]))

        # Filter by action=delete and search by query
        res_del = self.client.get("/api/workspace/history?action=delete")
        del_events = res_del.get_json()["events"]
        self.assertEqual([e["case_id"] for e in del_events], ["CASE-DEL"])
        self.assertEqual(del_events[0]["actor_name"], "Daniel")
        self.assertEqual(del_events[0]["actor_role"], "super_admin")
        self.assertEqual(del_events[0]["unit_number"], "555")
        self.assertEqual(del_events[0]["driver_name"], "Drop Driver")
        self.assertIsInstance(del_events[0].get("deleted_snapshot"), dict)
        self.assertEqual(del_events[0]["deleted_snapshot"]["issue_text"], "Delete me")

        res_q = self.client.get("/api/workspace/history?q=Love")
        self.assertEqual(len(res_q.get_json()["events"]), 1)

        # Developer actions expose actor_role='developer' on events and actors list, and dev sees deleted test cases
        c_dev_del = case_store.workspace_write(
            self.dev,
            {
                "id": "CASE-DEV-DEL",
                "request_id": "req-dev-del",
                "driver": "Dev Driver",
                "unit_number": "777",
                "issue": "Dev sensor calibration check",
                "vehicle_type": "truck",
                "priority": "normal",
                "target_agent": self.agent_a,
            },
        )
        case_store.workspace_write(self.dev, {"action": "set_test", "is_test": True}, c_dev_del["id"])
        case_store.remove_live_board_case(
            self.dev,
            c_dev_del["id"],
            mode="permanent",
            revision=case_store.workspace_revision(case_store.get_case(c_dev_del["id"])),
        )
        # Non-dev manager must NOT see deleted test case in deleted audit list
        mgr_ids = {e["case_id"] for e in self.client.get("/api/workspace/history").get_json()["events"]}
        self.assertNotIn("CASE-DEV-DEL", mgr_ids)

        # Developer sees test case events + deleted test case record + actor_role='developer'
        self._auth(self.dev)
        dev_body = self.client.get("/api/workspace/history").get_json()
        dev_ids = {e["case_id"] for e in dev_body["events"]}
        self.assertIn("CASE-TEST", dev_ids)
        self.assertIn("CASE-DEV-DEL", dev_ids)
        dev_del_ev = next(e for e in dev_body["events"] if e["case_id"] == "CASE-DEV-DEL")
        self.assertEqual(dev_del_ev["actor_name"], "Dev")
        self.assertEqual(dev_del_ev["actor_role"], "developer")
        self.assertEqual(dev_del_ev["unit_number"], "777")
        self.assertTrue(any(a.get("name") == "Dev" and a.get("role") == "developer" for a in dev_body["actors"]))


if __name__ == "__main__":
    unittest.main()
