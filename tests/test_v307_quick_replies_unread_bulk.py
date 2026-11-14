"""Live Board v307 tests: custom quick replies, unread comment markers/read-state, and Grid View bulk actions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.storage import case_store, db, preference_store


class LiveBoardV307BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        for target, attr in (
            (case_store, "_storage_dir"),
            (preference_store, "_storage_dir"),
        ):
            p = patch.object(target, attr, return_value=self.data_dir)
            p.start()
            self.addCleanup(p.stop)
        p_srv = patch.object(server, "DATA_DIR", self.data_dir)
        p_srv.start()
        self.addCleanup(p_srv.stop)

        self.daniel = {"id": 101, "name": "Daniel", "role": "super_admin", "username": "danielv"}
        self.alex = {"id": 102, "name": "Alex", "role": "agent", "username": "alexr"}
        with db.write_tx(self.data_dir) as conn:
            for person in (self.daniel, self.alex):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (person["id"], person["name"], person["username"], person["role"]),
                )

        self.c1 = case_store.workspace_write(
            self.daniel,
            {
                "id": "CASE-701",
                "request_id": "req-701",
                "driver": "Ben Sumner",
                "unit_number": "308",
                "issue": "Coolant leak",
                "vehicle_type": "truck",
                "priority": "normal",
                "target_agent": self.alex,
            },
        )
        self.c2 = case_store.workspace_write(
            self.daniel,
            {
                "id": "CASE-702",
                "request_id": "req-702",
                "driver": "Sam Lee",
                "unit_number": "419",
                "issue": "Reefer alarm A12",
                "vehicle_type": "reefer",
                "priority": "medium",
                "target_agent": self.daniel,
            },
        )
        # Alex writes a comment on CASE-701
        self.c1 = case_store.workspace_write(
            self.alex,
            {"action": "note", "revision": case_store.workspace_revision(self.c1), "text": "Shop ETA 2 hours", "file_ids": []},
            "CASE-701",
        )

    def _auth_session(self, client, user):
        with client.session_transaction() as sess:
            sess["user"] = {"id": user["id"], "name": user["name"], "role": user["role"]}
            sess["workspace_csrf"] = "test-csrf"

    def test_custom_quick_replies_crud_and_reset(self):
        client = server.app.test_client()
        self._auth_session(client, self.daniel)
        with patch.object(workspace_board, "get_user", return_value=self.daniel):
            res_get = client.get("/api/workspace/quick-replies")
            self.assertEqual(res_get.status_code, 200)
            self.assertIsNone(res_get.get_json()["quick_replies"])

            res_bad = client.put(
                "/api/workspace/quick-replies",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"quick_replies": [{"slash": "tow", "label": "", "text": "Dispatching tow"}]},
            )
            self.assertEqual(res_bad.status_code, 400)

            res_put = client.put(
                "/api/workspace/quick-replies",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={
                    "quick_replies": [
                        {"slash": "/Tow-Request", "label": "Tow Dispatched", "icon": "ph-truck", "text": "Heavy wrecker en route to unit {unit}."},
                        {"slash": "towrequest", "label": "Second", "icon": "bad-icon", "text": "Second template"},
                    ]
                },
            )
            self.assertEqual(res_put.status_code, 200)
            saved = res_put.get_json()["quick_replies"]
            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[0]["slash"], "tow-request")
            self.assertEqual(saved[0]["icon"], "ph-truck")
            self.assertEqual(saved[1]["icon"], "ph-lightning")
            self.assertNotEqual(saved[0]["slash"], saved[1]["slash"])

            res_reset = client.put(
                "/api/workspace/quick-replies",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"reset": True},
            )
            self.assertEqual(res_reset.status_code, 200)
            self.assertIsNone(res_reset.get_json()["quick_replies"])

    def test_board_note_markers_and_monotonic_read_state(self):
        client = server.app.test_client()
        self._auth_session(client, self.daniel)
        with patch.object(workspace_board, "get_user", return_value=self.daniel):
            res_board = client.get("/api/workspace/board")
            self.assertEqual(res_board.status_code, 200)
            by_id = {c["full_id"]: c for c in res_board.get_json()["cases"]}
            self.assertEqual(len(by_id["CASE-701"]["note_markers"]), 1)
            self.assertEqual(by_id["CASE-701"]["last_note_author_id"], str(self.alex["id"]))
            self.assertEqual(by_id["CASE-701"]["last_note_author"], "Alex")

            # Monotonic read state: newer timestamp wins over older
            r1 = client.post(
                "/api/workspace/read-state",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"case_id": "CASE-701", "seen_at": "2026-11-14T15:00:00+00:00"},
            )
            self.assertEqual(r1.status_code, 200)
            r2 = client.post(
                "/api/workspace/read-state",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"case_id": "CASE-701", "seen_at": "2026-11-14T12:00:00+00:00"},
            )
            self.assertEqual(r2.get_json()["case_reads"]["CASE-701"], "2026-11-14T15:00:00+00:00")

            with patch.object(workspace_board, "get_all_user_dicts", return_value=[self.daniel, self.alex]):
                res_agents = client.get("/api/workspace/agents")
            self.assertEqual(res_agents.status_code, 200)
            self.assertEqual(res_agents.get_json()["case_reads"]["CASE-701"], "2026-11-14T15:00:00+00:00")

    def test_bulk_update_permissions_and_close(self):
        client = server.app.test_client()
        # Non-manager agent Alex can update CASE-701 (assigned to him) but skips CASE-702 (Daniel's)
        self._auth_session(client, self.alex)
        with patch.object(workspace_board, "get_user", return_value=self.alex), \
             patch.object(workspace_board, "get_all_user_dicts", return_value=[self.daniel, self.alex]):
            res_agent = client.post(
                "/api/workspace/cases/bulk",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"case_ids": ["CASE-701", "CASE-702"], "priority": "high", "board_stage": "dealer_shop"},
            )
            self.assertEqual(res_agent.status_code, 200)
            body_agent = res_agent.get_json()
            self.assertEqual(body_agent["updated_ids"], ["CASE-701"])
            self.assertEqual(len(body_agent["skipped"]), 1)
            self.assertEqual(body_agent["skipped"][0]["id"], "CASE-702")

        # Manager Daniel can bulk-reassign & stage both, then bulk close both
        self._auth_session(client, self.daniel)
        with patch.object(workspace_board, "get_user", return_value=self.daniel), \
             patch.object(workspace_board, "get_all_user_dicts", return_value=[self.daniel, self.alex]):
            res_mgr = client.post(
                "/api/workspace/cases/bulk",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"case_ids": ["CASE-701", "CASE-702"], "agent_id": str(self.daniel["id"]), "board_stage": "at_yard", "priority": "low"},
            )
            self.assertEqual(res_mgr.status_code, 200)
            self.assertEqual(res_mgr.get_json()["updated_count"], 2)
            c1 = case_store.get_case("CASE-701")
            c2 = case_store.get_case("CASE-702")
            self.assertEqual(c1["agent_id"], 101)
            self.assertEqual(c1["board_stage"], "at_yard")
            self.assertEqual(c1["priority"], "low")
            self.assertEqual(c2["priority"], "low")

            res_close = client.post(
                "/api/workspace/cases/bulk",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"case_ids": ["CASE-701", "CASE-702"], "action": "close"},
            )
            self.assertEqual(res_close.status_code, 200)
            self.assertEqual(res_close.get_json()["updated_count"], 2)
            self.assertEqual(case_store.get_case("CASE-701")["status"], "done")
            self.assertEqual(case_store.get_case("CASE-702")["status"], "done")


if __name__ == "__main__":
    unittest.main()
