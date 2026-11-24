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
from backend.storage import board_presence, case_store, db, preference_store, user_store


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

    def test_canonical_actor_merging_excludes_legacy_placeholder_and_merges_aliases(self):
        # Create case with two legacy history entries for the same user under mixed handle/name/no-id
        c = case_store.workspace_write(
            self.manager,
            {
                "id": "CASE-ALIAS",
                "request_id": "req-alias",
                "driver": "Sam Driver",
                "unit_number": "410",
                "issue": "Wheel seal",
                "vehicle_type": "truck",
                "priority": "high",
                "target_agent": self.agent_a,
            },
        )
        # Inject mixed legacy author variants (username 'alexr' with no ID + display name 'Alex' with stale id)
        import json
        with db.write_tx(self.data_dir) as conn:
            row = conn.execute("SELECT data_json FROM cases WHERE id=?", (c["id"],)).fetchone()
            payload = json.loads(row["data_json"])
            payload.setdefault("workspace_history", []).extend([
                {"action": "edit", "author": "alexr", "author_id": "", "created_at": payload["opened_at"], "fields": ["shop_info"]},
                {"action": "edit", "author": "Alex", "author_id": "legacy-alex", "created_at": payload["opened_at"], "fields": ["amount"]},
            ])
            # Also add an unattributed deleted record marker (triggers Deleted Record (Legacy))
            conn.execute(
                "INSERT OR REPLACE INTO kv_state(key, data_json) VALUES (?,?)",
                ("workspace_deleted_records", json.dumps([{"case_id": "DEL-LEGACY-1", "unit_number": "901", "issue_text": "Old broken latch", "created_at": payload["opened_at"]}])),
            )
            conn.execute("UPDATE cases SET data_json=? WHERE id=?", (json.dumps(payload), c["id"]))

        self._auth(self.manager)
        body = self.client.get("/api/workspace/history").get_json()
        self.assertTrue(body["ok"])
        names = [a["name"] for a in body["actors"]]
        self.assertEqual(names.count("Alex"), 1, f"Alex duplicated in actors: {body['actors']}")
        alex_entry = next(a for a in body["actors"] if a["name"] == "Alex")
        self.assertEqual(alex_entry["count"], 2)
        self.assertNotIn("Deleted Record (Legacy)", names)
        self.assertTrue(any(e.get("case_id") == "DEL-LEGACY-1" for e in body["events"]))

    def test_realtime_sse_stream_and_presence_cleanup(self):
        import uuid
        sid = str(uuid.uuid4())
        sid_idle = str(uuid.uuid4())
        board_presence.heartbeat(sid, self.agent_a["id"], "Alex", "editing", "CASE-1", "shop_info", self.data_dir, draft="Love's")
        # Even if a second tab/transport writes a newer 'idle' heartbeat, 'editing' stays prioritized.
        board_presence.heartbeat(sid_idle, self.agent_a["id"], "Alex", "idle", "", "", self.data_dir)
        snap = board_presence.snapshot(self.manager["id"], self.data_dir)
        self.assertEqual(len(snap["peers"]), 1)
        self.assertEqual(snap["peers"][0]["action"], "editing")
        self.assertEqual(snap["online"], 1)

        self._auth(self.manager)
        agents_payload = self.client.get("/api/workspace/agents").get_json()
        alex_roster = next(a for a in agents_payload["agents"] if a["id"] == self.agent_a["id"])
        self.assertTrue(alex_roster["photo_url"].startswith("/api/avatar/"))

        res = self.client.get("/api/workspace/stream?max_seconds=1.0", headers={"Accept": "text/event-stream"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/event-stream", res.content_type)
        raw_sse = res.get_data(as_text=True)
        self.assertIn("event: ready", raw_sse)
        self.assertIn('"editing"', raw_sse)
        self.assertIn('"online": 2', raw_sse)

        # Action 'left' and remove_session clean up immediately
        board_presence.heartbeat(sid_idle, self.agent_a["id"], "Alex", "left", "", "", self.data_dir)
        board_presence.remove_session(sid, self.data_dir)
        snap2 = board_presence.snapshot(self.manager["id"], self.data_dir)
        self.assertEqual(snap2["peers"], [])

    def test_case_notes_normalize_canonical_author_names_and_sync_renames(self):
        # Simulate an agent whose notes contain legacy alias/username vs current display name
        uid = 33001
        user_store.add_user(uid, "Maxim_AH", "maximka", "agent")
        self.agent_m = {"id": uid, "name": "Maxim_AH", "role": "agent"}
        c = case_store.workspace_write(
            self.manager,
            {
                "id": "CASE-NOTES-MAXIM",
                "request_id": "req-maxim-1",
                "driver": "Max Driver",
                "unit_number": "712",
                "issue": "Coolant leak",
                "vehicle_type": "truck",
                "priority": "high",
                "target_agent": self.agent_m,
            },
        )
        import json
        with db.write_tx(self.data_dir) as conn:
            row = conn.execute("SELECT data_json FROM cases WHERE id=?", (c["id"],)).fetchone()
            payload = json.loads(row["data_json"])
            payload["workspace_notes"] = [
                {"id": "n1", "author": "maximka", "author_id": "", "author_role": "agent", "text": "Called shop", "created_at": payload["opened_at"]},
                {"id": "n2", "author": "Old Name", "author_id": str(uid), "author_role": "agent", "text": "ETA 2 hours", "created_at": payload["opened_at"]},
                {"id": "n3", "author": "Maxim_AH", "author_id": str(uid), "author_role": "agent", "text": "Approved", "created_at": payload["opened_at"]},
            ]
            conn.execute("UPDATE cases SET data_json=? WHERE id=?", (json.dumps(payload), c["id"]))

        self._auth(self.manager)
        detail = self.client.get(f"/api/case?id={c['id']}").get_json()
        authors = [n["author"] for n in detail["workspace_notes"]]
        author_ids = [n["author_id"] for n in detail["workspace_notes"]]
        self.assertEqual(authors, ["Maxim_AH", "Maxim_AH", "Maxim_AH"])
        self.assertEqual(author_ids, [str(uid), str(uid), str(uid)])

        # Also test propagating a future rename across stored notes
        updated_cnt = case_store.sync_user_display_on_cases(uid, "Maxim_AH_Renamed", "maxim_ah")
        self.assertEqual(updated_cnt, 1)
        stored = case_store.get_case(c["id"])
        self.assertEqual(stored["agent_name"], "Maxim_AH_Renamed")
        self.assertEqual(stored["workspace_notes"][1]["author"], "Maxim_AH_Renamed")

    def test_agent_recent_actions_and_version_endpoint(self):
        c1 = case_store.workspace_write(
            self.agent_a,
            {
                "id": "CASE-RECENT-1",
                "request_id": "req-recent-1",
                "driver": "Driver R1",
                "unit_number": "801",
                "issue": "Air line issue",
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
                "fields": {"shop_info": "TA #104"},
            },
            c1["id"],
        )
        case_store.workspace_write(
            self.agent_a,
            {
                "action": "note",
                "text": "Called TA #104",
                "revision": case_store.workspace_revision(c1),
            },
            c1["id"],
        )
        self._auth(self.agent_a)
        res = self.client.get(f"/api/workspace/agents/{self.agent_a['id']}/recent-actions?limit=5")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(len(data["events"]), 2)
        self.assertLessEqual(len(data["events"]), 5)
        self.assertTrue(all(ev["actor_id"] == str(self.agent_a["id"]) for ev in data["events"]))
        self.assertNotIn("deleted_snapshot", data["events"][0])

        ver = self.client.get("/api/workspace/version")
        self.assertEqual(ver.status_code, 200)
        self.assertTrue(bool(ver.get_json()["build_id"]))


if __name__ == "__main__":
    unittest.main()
