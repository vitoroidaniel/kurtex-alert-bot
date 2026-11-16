"""Live Board v318 backend tests: strict Test-lane auto-routing & AI/System suspect-test notes."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.core import test_case_ai
from backend.core.test_case_rules import (
    is_auto_test_report,
    is_suspect_test_candidate,
    is_test_group_name,
    should_auto_move_to_test_lane,
)
from backend.storage import case_store, db, preference_store, user_store


class LiveBoardV318TestLaneRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        for p in (
            patch.object(case_store, "_storage_dir", return_value=self.data_dir),
            patch.object(preference_store, "_storage_dir", return_value=self.data_dir),
            patch.object(db, "get_data_dir", return_value=self.data_dir),
            patch.object(user_store, "_storage_dir", return_value=self.data_dir),
            patch.object(user_store, "DATA_DIR", self.data_dir),
            patch.object(server, "DATA_DIR", self.data_dir),
            patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "", "CLOUDFLARE_API_TOKEN": ""}, clear=False),
        ):
            p.start()
            self.addCleanup(p.stop)
        user_store._invalidate_cache(self.data_dir)

        self.dev = {"id": 100, "name": "Daniel", "username": "danieldev", "role": "developer"}
        self.agent = {"id": 101, "name": "Alex", "username": "alex", "role": "agent"}
        with db.write_tx(self.data_dir) as conn:
            for u in (self.dev, self.agent):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (u["id"], u["name"], u["username"], u["role"]),
                )
        self.client = server.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = self.dev
            sess["workspace_csrf"] = "csrf-1"

    def _auth_patch(self):
        return patch.object(
            workspace_board,
            "get_user",
            side_effect=lambda uid: self.dev if str(uid) == "100" else self.agent,
        )

    def test_strict_auto_move_vs_suspect_candidate_rules(self):
        self.assertTrue(is_test_group_name("Test"))
        self.assertTrue(is_test_group_name("Testing"))
        self.assertTrue(is_test_group_name("Kurtex Test Group"))
        self.assertFalse(is_test_group_name("Detroit Fleet Dispatch"))

        # Strict auto-move: unit 1 / driver 1 / issue 1 or all-test
        self.assertTrue(should_auto_move_to_test_lane({"unit_number": "1", "report_driver": "1", "issue_text": "1"}))
        self.assertTrue(should_auto_move_to_test_lane({"unit_number": "test", "report_driver": "test", "issue_text": "test"}))
        self.assertTrue(should_auto_move_to_test_lane({"unit_number": "308", "report_driver": "John", "issue_text": "test"}))
        self.assertTrue(should_auto_move_to_test_lane({"unit_number": "308", "report_driver": "John", "issue_text": "test test test"}))
        self.assertTrue(should_auto_move_to_test_lane({"unit_number": "910", "issue_text": "this is just a test"}))

        # Real case coming from Test Group or partial test wording MUST NOT auto-move
        real_from_test_grp = {
            "group_name": "Kurtex Test Group",
            "unit_number": "R260",
            "report_driver": "Ihor",
            "issue_text": "Trailer ABS light stays on and air leak near rear axle",
        }
        self.assertFalse(should_auto_move_to_test_lane(real_from_test_grp))
        suspect, reason = is_suspect_test_candidate(real_from_test_grp)
        self.assertTrue(suspect)
        self.assertIn("Kurtex Test Group", reason)

        partial_test = {"unit_number": "TEST-1", "report_driver": "Ben", "issue_text": "Coolant leak"}
        self.assertFalse(should_auto_move_to_test_lane(partial_test))
        self.assertTrue(is_suspect_test_candidate(partial_test)[0])

        # Normal mechanical cases with diagnostic phrases trigger neither
        real_diag = {
            "group_name": "Drivers Maintenance",
            "unit_number": "R260",
            "driver_name": "Ihor K",
            "issue_text": "Brake chamber replacement and pressure test",
            "comments": "Shop performed road test after repair. Truck tested and running normally.",
        }
        self.assertFalse(should_auto_move_to_test_lane(real_diag))
        self.assertFalse(is_suspect_test_candidate(real_diag)[0])
        self.assertFalse(is_auto_test_report(real_diag))

    def test_telegram_reports_auto_move_only_dummy_all_test_and_annotate_real_from_test_group(self):
        # 1. Real case sent from a test group -> stays on main board (board_stage==""), gets System internal note
        created_grp = case_store.create_case("tg-grp-real", "Ihor", "ihor", "Kurtex Test Group", "Check engine SPN 3251")
        self.assertEqual(created_grp["board_stage"], "")
        case_store.assign_case("tg-grp-real", 101, "Alex", "alex")
        saved_grp = case_store.save_maintenance_report(
            "tg-grp-real",
            {
                "vehicle_type": "truck",
                "unit_number": "308",
                "driver": "Ihor",
                "issue": "Check engine light active fault SPN 3251",
                "load": "Empty",
                "priority": "high",
            },
            "report",
            [],
        )
        self.assertEqual(saved_grp["board_stage"], "")
        notes = saved_grp.get("workspace_notes") or []
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["author"], "System")
        self.assertEqual(notes[0]["author_id"], "system")
        self.assertEqual(notes[0]["author_role"], "system")
        self.assertTrue(notes[0]["system"])
        self.assertIn("Possible test case", notes[0]["text"])

        # 2. Unit 1 / driver 1 / issue 1 dummy report -> auto-moves to "test" lane (no system suspect note needed)
        case_store.create_case("tg-111", "1", None, "Drivers Group", "1")
        case_store.assign_case("tg-111", 101, "Alex", "alex")
        saved_111 = case_store.save_maintenance_report(
            "tg-111",
            {
                "vehicle_type": "truck",
                "unit_number": "1",
                "driver": "1",
                "issue": "1",
                "load": "Empty",
                "priority": "normal",
            },
            "report",
            [],
        )
        self.assertEqual(saved_111["board_stage"], "test")
        self.assertEqual(saved_111.get("workspace_notes") or [], [])

        # 3. Normal report with diagnostic "pressure test" stays out of Test and has no system note
        case_store.create_case("tg-real-1", "John", "john", "Drivers Group", "Coolant leak")
        case_store.assign_case("tg-real-1", 101, "Alex", "alex")
        saved_real = case_store.save_maintenance_report(
            "tg-real-1",
            {
                "vehicle_type": "truck",
                "unit_number": "777",
                "driver": "John",
                "issue": "Pressure test after hose repair",
                "load": "Empty",
                "priority": "normal",
            },
            "report",
            [],
        )
        self.assertEqual(saved_real["board_stage"], "")
        self.assertEqual(saved_real.get("workspace_notes") or [], [])

    def test_ai_identifies_suspect_test_and_adds_system_note_without_moving(self):
        with self._auth_patch():
            # Create a report with ambiguous/sample text that heuristics alone wouldn't flag
            res = self.client.post(
                "/api/workspace/cases",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={
                    "unit_number": "308",
                    "driver": "Daniel",
                    "issue": "checking onboarding flow abc",
                    "vehicle_type": "truck",
                    "priority": "normal",
                    "agent_id": "101",
                    "board_stage": "",
                    "request_id": "req-ai-suspect-1",
                },
            )
            self.assertEqual(res.status_code, 200, res.get_json())
            cid = res.get_json()["case"]["full_id"]
            self.assertEqual(res.get_json()["case"]["board_stage"], "")

            with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "acc", "CLOUDFLARE_API_TOKEN": "tok"}), \
                 patch.object(test_case_ai, "classify_suspect_test_with_ai", return_value=("maybe_test", "Sample onboarding phrase detected")):
                annotated = test_case_ai.evaluate_and_annotate_case(cid, expected_storage_dir=self.data_dir)
                self.assertTrue(annotated)
                # Idempotent: running again does not duplicate
                self.assertFalse(test_case_ai.evaluate_and_annotate_case(cid, expected_storage_dir=self.data_dir))

            updated = case_store.get_case(cid)
            self.assertEqual(updated["board_stage"], "")  # Never auto-moved by AI!
            sys_notes = [n for n in updated.get("workspace_notes", []) if n.get("kind") == "test_suspect"]
            self.assertEqual(len(sys_notes), 1)
            self.assertIn("AI check", sys_notes[0]["text"])
            self.assertIn("Sample onboarding phrase detected", sys_notes[0]["text"])

            # Verify detail API keeps author_role=="system" and board API omits system note from unread note_markers
            detail = self.client.get(f"/api/case?id={cid}").get_json()
            self.assertEqual(detail["workspace_notes"][0]["author_role"], "system")
            board_row = next(r for r in self.client.get("/api/workspace/board").get_json()["cases"] if r["full_id"] == cid)
            self.assertEqual(board_row["note_markers"], [])

    def test_workspace_create_dummy_routes_to_test_and_manual_override_holds(self):
        with self._auth_patch():
            del_res = self.client.delete("/api/workspace/lanes/test", headers={"X-Workspace-CSRF": "csrf-1"})
            self.assertEqual(del_res.status_code, 400)

            # Pure unit 1 / driver 1 / issue 1 auto-moves to test lane
            c_res = self.client.post(
                "/api/workspace/cases",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={
                    "unit_number": "1",
                    "driver": "1",
                    "issue": "1",
                    "vehicle_type": "truck",
                    "priority": "normal",
                    "agent_id": "101",
                    "board_stage": "",
                    "request_id": "req-dummy-111",
                },
            )
            self.assertEqual(c_res.status_code, 200, c_res.get_json())
            c_json = c_res.get_json()
            cid = c_json["case"]["full_id"]
            self.assertEqual(c_json["case"]["board_stage"], "test")

            mv_res = self.client.patch(
                f"/api/workspace/cases/{cid}",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={"action": "stage", "stage": "dealer_shop", "revision": c_json["revision"]},
            )
            self.assertEqual(mv_res.status_code, 200, mv_res.get_json())
            self.assertTrue(case_store.get_case(cid).get("test_lane_overridden"))

            # Normal case edited to all-test auto-moves to "test"
            norm_res = self.client.post(
                "/api/workspace/cases",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={
                    "unit_number": "411",
                    "driver": "Sam",
                    "issue": "Alternator issue",
                    "vehicle_type": "truck",
                    "priority": "high",
                    "agent_id": "101",
                    "board_stage": "",
                    "request_id": "req-norm-1",
                },
            )
            self.assertEqual(norm_res.status_code, 200, norm_res.get_json())
            norm_json = norm_res.get_json()
            edit_res = self.client.patch(
                f"/api/workspace/cases/{norm_json['case']['full_id']}",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={
                    "action": "edit",
                    "revision": norm_json["revision"],
                    "fields": {"issue_text": "test"},
                    "base_fields": {"issue_text": "Alternator issue"},
                },
            )
            self.assertEqual(edit_res.status_code, 200, edit_res.get_json())
            self.assertEqual(edit_res.get_json()["case"]["board_stage"], "test")

    def test_v2_migration_reverts_real_test_group_case_and_keeps_111_in_test(self):
        legacy_dir = Path(self.temp.name) / "existing_db"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        with db.write_tx(legacy_dir) as conn:
            conn.execute("DELETE FROM meta WHERE key='test_lane_routed_v2'")
            conn.execute("UPDATE meta SET value='3' WHERE key='live_board_schema_version'")
            # Real breakdown from a test group that v1 mistakenly moved into board_stage="test"
            real_in_test = {
                "id": "old-real-grp",
                "status": "reported",
                "group_name": "Test Group",
                "driver_name": "Ihor",
                "unit_number": "R260",
                "issue_text": "Coolant leak at thermostat housing",
                "board_stage": "test",
                "reported_at": "2026-11-15T10:00:00+00:00",
            }
            dummy_111 = {
                "id": "old-dummy-111",
                "status": "reported",
                "group_name": "Drivers",
                "driver_name": "1",
                "unit_number": "1",
                "issue_text": "1",
                "board_stage": "",
                "reported_at": "2026-11-15T10:05:00+00:00",
            }
            for item in (real_in_test, dummy_111):
                conn.execute(
                    "INSERT INTO cases(id,status,agent_id,reported_at,group_name,unit_number,driver_name,board_stage,data_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (item["id"], "reported", 101, item["reported_at"], item["group_name"], item["unit_number"], item["driver_name"], item["board_stage"], json.dumps(item)),
                )

        db.reset_init_cache()
        with db.read_conn(legacy_dir) as conn:
            row_real = conn.execute("SELECT board_stage, data_json FROM cases WHERE id='old-real-grp'").fetchone()
            row_111 = conn.execute("SELECT board_stage, data_json FROM cases WHERE id='old-dummy-111'").fetchone()
            self.assertEqual(row_real["board_stage"], "")
            real_json = json.loads(row_real["data_json"])
            self.assertEqual(real_json["board_stage"], "")
            self.assertEqual(len(real_json.get("workspace_notes") or []), 1)
            self.assertEqual(real_json["workspace_notes"][0]["author_role"], "system")
            self.assertEqual(row_111["board_stage"], "test")


if __name__ == "__main__":
    unittest.main()
