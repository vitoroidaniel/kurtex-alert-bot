"""Live Board v310 tests: Developer test-case marking, read-only lock, search/AI exclusion, and shift status."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.storage import case_store, db, preference_store, shift_store, user_store


class LiveBoardV310Tests(unittest.TestCase):
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
        p_db = patch.object(db, "get_data_dir", return_value=self.data_dir)
        p_db.start()
        self.addCleanup(p_db.stop)
        p_ustore = patch.object(user_store, "DATA_DIR", self.data_dir)
        p_ustore.start()
        self.addCleanup(p_ustore.stop)
        p_srv = patch.object(server, "DATA_DIR", self.data_dir)
        p_srv.start()
        self.addCleanup(p_srv.stop)

        self.dev = {"id": 100, "name": "Dev Dan", "role": "developer", "username": "devdan"}
        self.manager = {"id": 101, "name": "Manager Max", "role": "super_admin", "username": "mmax"}
        self.alex = {"id": 102, "name": "Alex", "role": "agent", "username": "alexr"}
        self.sam = {"id": 103, "name": "Sam", "role": "agent", "username": "samk"}
        with db.write_tx(self.data_dir) as conn:
            for person in (self.dev, self.manager, self.alex, self.sam):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (person["id"], person["name"], person["username"], person["role"]),
                )

        self.case_a = case_store.workspace_write(
            self.dev,
            {
                "id": "CASE-V310-1",
                "request_id": "req-v310-1",
                "driver": "Ben Sumner",
                "unit_number": "308",
                "issue": "Coolant leak at thermostat housing",
                "vehicle_type": "truck",
                "priority": "high",
                "board_stage": "",
                "target_agent": self.alex,
            },
        )
        self.case_real_closed = case_store.workspace_write(
            self.dev,
            {
                "id": "CASE-V310-2",
                "request_id": "req-v310-2",
                "driver": "Tim Hale",
                "unit_number": "412",
                "issue": "Coolant leak at thermostat housing hose clamp",
                "vehicle_type": "truck",
                "priority": "normal",
                "board_stage": "",
                "target_agent": self.alex,
            },
        )
        case_store.workspace_write(
            self.dev,
            {
                "action": "edit",
                "revision": case_store.workspace_revision(self.case_real_closed),
                "fields": {"shop_info": "Loves Truck Care #401 · Joplin MO · Bay 4"},
            },
            "CASE-V310-2",
        )
        self.case_real_closed = case_store.get_case("CASE-V310-2")
        case_store.workspace_write(
            self.dev,
            {"action": "close", "revision": case_store.workspace_revision(self.case_real_closed)},
            "CASE-V310-2",
        )

    def _auth_session(self, client, user):
        with client.session_transaction() as sess:
            sess["user"] = {"id": user["id"], "name": user["name"], "role": user["role"]}
            sess["workspace_csrf"] = "test-csrf"

    def test_only_developer_can_toggle_test_and_locks_non_devs_and_excludes_from_search_and_ai(self):
        client = server.app.test_client()
        headers = {"X-Workspace-CSRF": "test-csrf"}

        # Regular agent and manager cannot mark a case as test
        for non_dev in (self.alex, self.manager):
            self._auth_session(client, non_dev)
            with patch.object(workspace_board, "get_user", return_value=non_dev):
                res = client.patch(
                    "/api/workspace/cases/CASE-V310-1",
                    headers=headers,
                    json={"action": "set_test", "is_test": True},
                )
                self.assertEqual(res.status_code, 403)

        # Developer marks CASE-V310-1 and closed CASE-V310-2 (for AI/search check) as test
        self._auth_session(client, self.dev)
        with patch.object(workspace_board, "get_user", return_value=self.dev):
            res = client.patch(
                "/api/workspace/cases/CASE-V310-1",
                headers=headers,
                json={"action": "set_test", "is_test": True},
            )
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json()["case"]["is_test"])

            # Long shop_info > 200 chars works for developer
            long_shop = "Premier Truck Repair · 1234 Freight Way, Dallas TX · Bay 7 · Contact Mike (555) 222-3344 · Waiting PO approval " + ("x" * 180)
            edit_res = client.patch(
                "/api/workspace/cases/CASE-V310-1",
                headers=headers,
                json={
                    "action": "edit",
                    "revision": res.get_json()["revision"],
                    "fields": {"shop_info": long_shop},
                },
            )
            self.assertEqual(edit_res.status_code, 200)
            self.assertEqual(edit_res.get_json()["case"]["shop_info"], long_shop)

        # Assigned agent AND super_admin now have read-only access on CASE-V310-1
        for non_dev in (self.alex, self.manager):
            self._auth_session(client, non_dev)
            with patch.object(workspace_board, "get_user", return_value=non_dev):
                detail = client.get("/api/case?id=CASE-V310-1").get_json()
                self.assertTrue(detail["is_test"])
                self.assertFalse(detail["can_manage"])
                self.assertFalse(detail["can_reassign"])
                self.assertFalse(detail["can_comment"])
                self.assertFalse(detail["can_pin"])
                self.assertFalse(detail["can_toggle_test"])

                board = client.get("/api/workspace/board").get_json()["cases"]
                row = next(c for c in board if c["full_id"] == "CASE-V310-1")
                self.assertTrue(row["is_test"])
                self.assertFalse(row["can_manage"])
                self.assertFalse(row["can_comment"])

                note_res = client.post(
                    "/api/workspace/cases/CASE-V310-1",
                    json={},
                ) if False else client.patch(
                    "/api/workspace/cases/CASE-V310-1",
                    headers=headers,
                    json={"action": "note", "revision": detail["revision"], "text": "trying to write"},
                )
                self.assertEqual(note_res.status_code, 403)

                edit_res = client.patch(
                    "/api/workspace/cases/CASE-V310-1",
                    headers=headers,
                    json={"action": "edit", "revision": detail["revision"], "fields": {"priority": "low"}},
                )
                self.assertEqual(edit_res.status_code, 403)

                upload_res = client.post(
                    "/api/workspace/cases/CASE-V310-1/attachments",
                    headers=headers,
                    data={"file": (io.BytesIO(b"hello"), "proof.txt")},
                    content_type="multipart/form-data",
                )
                self.assertEqual(upload_res.status_code, 403)

        # Search in /api/cases excludes test cases
        self._auth_session(client, self.alex)
        with patch.object(workspace_board, "get_user", return_value=self.alex):
            search_res = client.get("/api/cases?filter=all&search=308").get_json()["cases"]
            self.assertFalse(any(c["full_id"] == "CASE-V310-1" for c in search_res))

        # AI insights never return test cases as similar resolved cases
        target_real = case_store.workspace_write(
            self.dev,
            {
                "id": "CASE-V310-3",
                "request_id": "req-v310-3",
                "driver": "Rob Lee",
                "unit_number": "515",
                "issue": "Coolant leak at thermostat housing",
                "vehicle_type": "truck",
                "priority": "high",
                "board_stage": "",
                "target_agent": self.alex,
            },
        )
        self._auth_session(client, self.dev)
        with patch.object(workspace_board, "get_user", return_value=self.dev):
            ai_before = client.get("/api/workspace/cases/CASE-V310-3/ai-insights").get_json()
            self.assertTrue(any(s["id"] == "CASE-V310-2" for s in ai_before.get("similar_cases", [])))

            # Mark CASE-V310-2 as test -> disappears from AI insights!
            mark_closed = client.patch(
                "/api/workspace/cases/CASE-V310-2",
                headers=headers,
                json={"action": "set_test", "is_test": True},
            )
            self.assertEqual(mark_closed.status_code, 200)
            ai_after = client.get("/api/workspace/cases/CASE-V310-3/ai-insights").get_json()
            self.assertFalse(any(s["id"] == "CASE-V310-2" for s in ai_after.get("similar_cases", [])))
            # And the test case itself gets no AI suggestions
            ai_test_target = client.get("/api/workspace/cases/CASE-V310-1/ai-insights").get_json()
            self.assertEqual(ai_test_target.get("similar_cases"), [])

    def test_workspace_agents_exposes_shift_duty_state(self):
        client = server.app.test_client()
        self._auth_session(client, self.alex)
        mocked_shift = {
            "shifts": [{"id": "s1", "name": "Day Shift", "enabled": True}],
            "active_shifts": [
                {
                    "id": "s1",
                    "name": "Day Shift",
                    "start_time": "06:00",
                    "end_time": "18:00",
                    "user_ids": [self.alex["id"]],
                }
            ],
            "on_duty_users": [self.alex],
            "on_duty_user_ids": [self.alex["id"]],
            "has_configured_shifts": True,
            "using_fallback": False,
        }
        with (
            patch.object(workspace_board, "get_user", return_value=self.alex),
            patch.object(workspace_board, "get_all_user_dicts", return_value=[self.dev, self.manager, self.alex, self.sam]),
            patch.object(shift_store, "get_on_shift_state", return_value=mocked_shift),
        ):
            payload = client.get("/api/workspace/agents").get_json()
            self.assertTrue(payload["has_configured_shifts"])
            self.assertFalse(payload["using_shift_fallback"])
            by_id = {str(a["id"]): a for a in payload["agents"]}
            self.assertTrue(by_id[str(self.alex["id"])]["on_duty"])
            self.assertTrue(by_id[str(self.alex["id"])]["shift_managed"])
            self.assertEqual(by_id[str(self.alex["id"])]["shift_names"], ["Day Shift"])
            self.assertFalse(by_id[str(self.sam["id"])]["on_duty"])
            self.assertTrue(by_id[str(self.sam["id"])]["shift_managed"])
            self.assertTrue(by_id[str(self.manager["id"])]["shift_managed"])
            self.assertFalse(by_id[str(self.dev["id"])]["shift_managed"])

    def test_workspace_note_write_returns_normalized_note(self):
        client = server.app.test_client()
        headers = {"X-Workspace-CSRF": "test-csrf"}
        self._auth_session(client, self.alex)
        with patch.object(workspace_board, "get_user", return_value=self.alex):
            detail = client.get("/api/case?id=CASE-V310-1").get_json()
            res = client.patch(
                "/api/workspace/cases/CASE-V310-1",
                headers=headers,
                json={
                    "action": "note",
                    "revision": detail["revision"],
                    "text": "Called shop — waiting on ETA",
                },
            )
            self.assertEqual(res.status_code, 200)
            body = res.get_json()
            self.assertIsInstance(body.get("note"), dict)
            self.assertEqual(body["note"].get("text"), "Called shop — waiting on ETA")
            self.assertTrue(body["note"].get("id"))
            self.assertTrue(body["note"].get("created_at"))
            refetched = client.get("/api/case?id=CASE-V310-1").get_json()
            self.assertEqual(refetched["workspace_notes"][-1]["id"], body["note"]["id"])
            self.assertEqual(refetched["workspace_notes"][-1]["text"], "Called shop — waiting on ETA")


if __name__ == "__main__":
    unittest.main()
