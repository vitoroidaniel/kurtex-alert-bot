"""Live Board v318 backend tests: built-in Test lane & auto-routing test reports/groups."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.core.test_case_rules import is_auto_test_report, is_test_group_name
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

    def test_detection_rules_ignore_real_mechanical_phrases(self):
        self.assertTrue(is_test_group_name("Test"))
        self.assertTrue(is_test_group_name("Testing"))
        self.assertTrue(is_test_group_name("Kurtex Test Group"))
        self.assertFalse(is_test_group_name("Detroit Fleet Dispatch"))

        self.assertTrue(is_auto_test_report({"group_name": "Testing", "issue_text": "Flat tire"}))
        self.assertTrue(is_auto_test_report({"unit_number": "308", "issue_text": "test"}))
        self.assertTrue(is_auto_test_report({"unit_number": "308", "issue_text": "testing brakes please ignore"}))
        self.assertTrue(is_auto_test_report({"unit_number": "TEST-1", "issue_text": "Coolant leak"}))
        self.assertTrue(is_auto_test_report({"unit_number": "308", "issue_text": "#test air leak"}))

        self.assertFalse(is_auto_test_report({
            "group_name": "Drivers Maintenance",
            "unit_number": "R260",
            "driver_name": "Ihor K",
            "issue_text": "Brake chamber replacement and pressure test",
            "comments": "Shop performed road test after repair. Truck tested and running normally.",
        }))
        self.assertFalse(is_auto_test_report({
            "group_name": "Reefer Support",
            "unit_number": "412",
            "driver_name": "Mike",
            "issue_text": "Battery load test failed on starter",
        }))

    def test_telegram_report_from_test_group_or_test_text_auto_moves_to_test_lane(self):
        # 1. Alert created in a test group
        created_grp = case_store.create_case("tg-grp-1", "John", "john", "Kurtex Test Group", "Check engine")
        self.assertEqual(created_grp["board_stage"], "test")
        case_store.assign_case("tg-grp-1", 101, "Alex", "alex")
        saved_grp = case_store.save_maintenance_report(
            "tg-grp-1",
            {
                "vehicle_type": "truck",
                "unit_number": "308",
                "driver": "John",
                "issue": "Check engine light",
                "load": "Empty",
                "priority": "normal",
            },
            "report",
            [],
        )
        self.assertEqual(saved_grp["board_stage"], "test")

        # 2. Alert from normal group, but report text says "test"
        case_store.create_case("tg-txt-1", "John", "john", "Drivers Group", "Need help")
        case_store.assign_case("tg-txt-1", 101, "Alex", "alex")
        saved_txt = case_store.save_maintenance_report(
            "tg-txt-1",
            {
                "vehicle_type": "truck",
                "unit_number": "501",
                "driver": "John",
                "issue": "just a test",
                "load": "Empty",
                "priority": "normal",
            },
            "report",
            [],
        )
        self.assertEqual(saved_txt["board_stage"], "test")

        # 3. Normal report stays in agent lane (board_stage == "")
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

    def test_workspace_create_edit_and_manual_override(self):
        with self._auth_patch():
            # Built-in test stage cannot be deleted
            del_res = self.client.delete(
                "/api/workspace/lanes/test",
                headers={"X-Workspace-CSRF": "csrf-1"},
            )
            self.assertEqual(del_res.status_code, 400)

            # Web New Case with "test" issue while agent_id is set and board_stage="" -> auto-routes to "test"
            c_res = self.client.post(
                "/api/workspace/cases",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={
                    "unit_number": "902",
                    "driver": "Mike",
                    "issue": "test bot integration",
                    "vehicle_type": "truck",
                    "priority": "normal",
                    "agent_id": "101",
                    "board_stage": "",
                    "request_id": "req-test-1",
                },
            )
            self.assertEqual(c_res.status_code, 200, c_res.get_json())
            c_json = c_res.get_json()
            case_obj = c_json["case"]
            cid = case_obj["full_id"]
            self.assertEqual(case_obj["board_stage"], "test")

            # Manual move OUT of "test" to "dealer_shop" sets test_lane_overridden
            mv_res = self.client.patch(
                f"/api/workspace/cases/{cid}",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={"action": "stage", "stage": "dealer_shop", "revision": c_json["revision"]},
            )
            self.assertEqual(mv_res.status_code, 200, mv_res.get_json())
            moved = mv_res.get_json()["case"]
            self.assertEqual(moved["board_stage"], "dealer_shop")
            self.assertTrue(case_store.get_case(cid).get("test_lane_overridden"))

            # Normal case edited to have test text auto-moves to "test"
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
            norm_case = norm_json["case"]
            self.assertEqual(norm_case["board_stage"], "")

            edit_res = self.client.patch(
                f"/api/workspace/cases/{norm_case['full_id']}",
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

    def test_existing_active_test_reports_migrated_on_db_init(self):
        # Create an already-initialized v2 DB before "test_lane_routed_v1" existed, with an existing test report
        legacy_dir = Path(self.temp.name) / "existing_db"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        sqlite_path = legacy_dir / "kurtex.sqlite3"
        with db.write_tx(legacy_dir) as conn:
            conn.execute("DELETE FROM meta WHERE key='test_lane_routed_v1'")
            conn.execute("UPDATE meta SET value='2' WHERE key='live_board_schema_version'")
            old_test = {
                "id": "old-tg-test",
                "status": "reported",
                "group_name": "Test Group",
                "driver_name": "Driver A",
                "unit_number": "101",
                "issue_text": "Air leak check",
                "board_stage": "",
                "reported_at": "2026-11-15T10:00:00+00:00",
            }
            old_real = {
                "id": "old-tg-real",
                "status": "reported",
                "group_name": "Fleet Maint",
                "driver_name": "Driver B",
                "unit_number": "102",
                "issue_text": "Pressure test after radiator hose repair",
                "board_stage": "",
                "reported_at": "2026-11-15T10:05:00+00:00",
            }
            for item in (old_test, old_real):
                conn.execute(
                    "INSERT INTO cases(id,status,agent_id,reported_at,group_name,unit_number,driver_name,board_stage,data_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (item["id"], "reported", 101, item["reported_at"], item["group_name"], item["unit_number"], item["driver_name"], "", json.dumps(item)),
                )

        # Simulate fresh process boot against existing DB
        db.reset_init_cache()
        with db.read_conn(legacy_dir) as conn:
            row_test = conn.execute("SELECT board_stage, data_json FROM cases WHERE id='old-tg-test'").fetchone()
            row_real = conn.execute("SELECT board_stage, data_json FROM cases WHERE id='old-tg-real'").fetchone()
            self.assertEqual(row_test["board_stage"], "test")
            self.assertEqual(json.loads(row_test["data_json"])["board_stage"], "test")
            self.assertEqual(row_real["board_stage"], "")


if __name__ == "__main__":
    unittest.main()
