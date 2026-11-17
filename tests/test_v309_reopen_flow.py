"""Live Board v309 tests: clean Done -> Reopen case workflow & permissions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.storage import case_store, db, preference_store


class LiveBoardV309ReopenTests(unittest.TestCase):
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
        self.mike = {"id": 103, "name": "Mike", "role": "agent", "username": "miket"}
        with db.write_tx(self.data_dir) as conn:
            for person in (self.daniel, self.alex, self.mike):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (person["id"], person["name"], person["username"], person["role"]),
                )

        self.c1 = case_store.workspace_write(
            self.daniel,
            {
                "id": "CASE-901",
                "request_id": "req-901",
                "driver": "Ben Sumner",
                "unit_number": "308",
                "issue": "Coolant leak",
                "vehicle_type": "truck",
                "priority": "high",
                "board_stage": "dealer_shop",
                "target_agent": self.alex,
            },
        )

    def _auth_session(self, client, user):
        with client.session_transaction() as sess:
            sess["user"] = {"id": user["id"], "name": user["name"], "role": user["role"]}
            sess["workspace_csrf"] = "test-csrf"

    def test_reopen_closed_case_restores_board_and_records_history(self):
        client = server.app.test_client()
        self._auth_session(client, self.alex)
        headers = {"X-Workspace-CSRF": "test-csrf"}
        with patch.object(workspace_board, "get_user", return_value=self.alex):
            close_res = client.patch(
                "/api/workspace/cases/CASE-901",
                headers=headers,
                json={"action": "close", "revision": case_store.workspace_revision(self.c1)},
            )
            self.assertEqual(close_res.status_code, 200)
            closed_detail = client.get("/api/case?id=CASE-901").get_json()
            self.assertEqual(closed_detail["status"], "done")
            self.assertEqual(closed_detail["closed_by_name"], "Alex")

            board_while_done = client.get("/api/workspace/board").get_json()["cases"]
            self.assertFalse(any(c["full_id"] == "CASE-901" for c in board_while_done))

            reopen_res = client.patch(
                "/api/workspace/cases/CASE-901",
                headers=headers,
                json={"action": "reopen", "revision": closed_detail["revision"]},
            )
            self.assertEqual(reopen_res.status_code, 200)
            reopened = reopen_res.get_json()["case"]
            self.assertEqual(reopened["status"], "reported")
            self.assertEqual(reopened["closed"], server.fmt_dt(None))
            self.assertIsNone(case_store.get_case("CASE-901").get("closed_at"))

            detail_after = client.get("/api/case?id=CASE-901").get_json()
            self.assertEqual(detail_after["status"], "reported")
            self.assertEqual(detail_after["reopened_by_name"], "Alex")
            self.assertEqual(detail_after["board_stage"], "dealer_shop")
            self.assertFalse(detail_after["can_view_history"])
            self.assertEqual(detail_after["workspace_history"], [])
            # History is retained in storage, but only managers/developers may view it.
            self.assertTrue(
                any(h.get("action") == "reopen" and h.get("from") == "done" and h.get("to") == "reported"
                    for h in case_store.get_case("CASE-901")["workspace_history"])
            )

            board_after = client.get("/api/workspace/board").get_json()["cases"]
            self.assertTrue(any(c["full_id"] == "CASE-901" for c in board_after))
            done_after = client.get("/api/cases?filter=workspace_closed").get_json()["cases"]
            self.assertFalse(any(c["full_id"] == "CASE-901" for c in done_after))

    def test_unrelated_agent_cannot_reopen_and_reopen_is_idempotent(self):
        closed = case_store.workspace_write(
            self.alex,
            {"action": "close", "revision": case_store.workspace_revision(self.c1)},
            "CASE-901",
        )
        client = server.app.test_client()
        self._auth_session(client, self.mike)
        with patch.object(workspace_board, "get_user", return_value=self.mike):
            res_forbidden = client.patch(
                "/api/workspace/cases/CASE-901",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"action": "reopen", "revision": case_store.workspace_revision(closed)},
            )
            self.assertEqual(res_forbidden.status_code, 403)

        self._auth_session(client, self.daniel)
        with patch.object(workspace_board, "get_user", return_value=self.daniel):
            res_ok = client.patch(
                "/api/workspace/cases/CASE-901",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"action": "reopen", "revision": case_store.workspace_revision(closed)},
            )
            self.assertEqual(res_ok.status_code, 200)
            res_idem = client.patch(
                "/api/workspace/cases/CASE-901",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"action": "reopen", "revision": res_ok.get_json()["revision"]},
            )
            self.assertEqual(res_idem.status_code, 200)


if __name__ == "__main__":
    unittest.main()
