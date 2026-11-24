"""Live Board v306 tests: rich history previews/filenames and AI case insights."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.storage import case_store, db


class LiveBoardHistoryAndAITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.patch_case_dir = patch.object(case_store, "_storage_dir", return_value=self.data_dir)
        self.patch_server_dir = patch.object(server, "DATA_DIR", self.data_dir)
        self.patch_case_dir.start()
        self.addCleanup(self.patch_case_dir.stop)
        self.patch_server_dir.start()
        self.addCleanup(self.patch_server_dir.stop)

        self.daniel = {"id": 101, "name": "Daniel", "role": "super_admin", "username": "danikav"}
        with db.write_tx(self.data_dir) as conn:
            conn.execute(
                "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                (self.daniel["id"], self.daniel["name"], self.daniel["username"], self.daniel["role"]),
            )

        self.target = case_store.workspace_write(
            self.daniel,
            {
                "id": "case-ai-target",
                "request_id": "req-target",
                "driver": "Ben Sumner",
                "unit_number": "308",
                "issue": "Coolant leak near radiator hose and overheating",
                "vehicle_type": "truck",
                "priority": "high",
            },
        )
        # Historical resolved case with same symptoms & shop
        with db.write_tx(self.data_dir) as conn:
            for c in (
                {
                    "id": "case-hist-1",
                    "case_type": "maintenance_report",
                    "unit_number": "308",
                    "vehicle_type": "truck",
                    "issue_text": "Coolant leak near radiator hose and overheating",
                    "description": "Coolant leak at upper clamp",
                    "shop_info": "TA Truck Service #142",
                    "amount": "640.00",
                    "status": "done",
                    "reported_at": "2026-11-05T10:00:00+00:00",
                    "closed_at": "2026-11-05T16:30:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Daniel",
                    "workspace_notes": [{"id": "n1", "text": "Replaced upper radiator clamp and refilled coolant"}],
                },
                {
                    "id": "case-hist-2",
                    "case_type": "maintenance_report",
                    "unit_number": "419",
                    "vehicle_type": "truck",
                    "issue_text": "Overheating radiator thermostat coolant leak",
                    "description": "Coolant spraying on highway",
                    "shop_info": "TA Truck Service #142",
                    "amount": "920.00",
                    "status": "done",
                    "reported_at": "2026-10-20T09:00:00+00:00",
                    "closed_at": "2026-10-20T18:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Daniel",
                    "notes": "Thermostat housing gasket replaced",
                },
            ):
                conn.execute(
                    "INSERT INTO cases(id,status,agent_id,data_json) VALUES(?,?,?,?)",
                    (c["id"], c["status"], c["agent_id"], json.dumps(c)),
                )

    def test_history_captures_note_preview_and_attachment_filename(self):
        rev = case_store.workspace_revision(self.target)
        after_note = case_store.workspace_write(
            self.daniel,
            {"action": "note", "revision": rev, "text": "Called TA #142 bay 3 for coolant hose"},
            "case-ai-target",
        )
        self.assertEqual(after_note["workspace_history"][-1]["action"], "note")
        self.assertIn("Called TA #142", after_note["workspace_history"][-1]["preview"])
        nid = after_note["workspace_notes"][0]["id"]

        after_del = case_store.workspace_write(
            self.daniel,
            {"action": "note_delete", "revision": case_store.workspace_revision(after_note), "note_id": nid},
            "case-ai-target",
        )
        self.assertEqual(after_del["workspace_notes"][0]["text"], "")
        self.assertIn("Called TA #142", after_del["workspace_history"][-1]["preview"])

        case_store.workspace_attachment_write(
            self.daniel,
            "case-ai-target",
            attachment={
                "id": "b" * 32,
                "name": "radiator_leak.jpg",
                "extension": ".jpg",
                "category": "attachment",
                "uploaded_by": "Daniel",
                "uploaded_by_id": "101",
            },
        )
        latest = case_store.get_case("case-ai-target")
        self.assertEqual(latest["workspace_history"][-1]["action"], "attachment_added")
        self.assertEqual(latest["workspace_history"][-1]["file_name"], "radiator_leak.jpg")

    def test_ai_insights_uses_close_matches_and_notes_for_troubleshooting(self):
        with db.write_tx(self.data_dir) as conn:
            unrelated = {
                "id": "case-same-unit-unrelated",
                "unit_number": "308",
                "vehicle_type": "truck",
                "issue_text": "Starter failure, no crank",
                "status": "done",
                "notes": "Radiator hose inspected during earlier maintenance.",
            }
            conn.execute(
                "INSERT INTO cases(id,status,agent_id,data_json) VALUES(?,?,?,?)",
                (unrelated["id"], "done", 101, json.dumps(unrelated)),
            )
        case_store.workspace_write(self.daniel, {
            "action": "note",
            "revision": case_store.workspace_revision(self.target),
            "text": "Driver confirmed visible coolant near the upper clamp.",
        }, "case-ai-target")
        client = server.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 101, "name": "Daniel", "role": "super_admin"}
        with patch.object(workspace_board, "get_user", return_value=self.daniel), \
             patch.object(server, "load_cases", side_effect=lambda: case_store.get_all_cases()):
            res = client.get("/api/workspace/cases/case-ai-target/ai-insights")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["unit_insights"]["unit_number"], "308")
        self.assertGreaterEqual(body["unit_insights"]["resolved"], 1)
        self.assertEqual(body["suggested_shops"], [])
        self.assertEqual(body["triage"]["latest_note"], "Driver confirmed visible coolant near the upper clamp.")
        self.assertTrue(any("temperature" in step for step in body["triage"]["troubleshooting_steps"]))
        self.assertEqual(body["cost_estimate"]["min"], 640.0)
        similar_ids = [match["id"] for match in body["similar_cases"]]
        self.assertIn("case-hist-1", similar_ids)
        self.assertNotIn("case-same-unit-unrelated", similar_ids)
        self.assertTrue(all(match.get("match_reason") for match in body["similar_cases"]))
        self.assertNotIn("case-ai-target", similar_ids)

        # Optional LLM request falls back cleanly when Cloudflare AI fails or is unconfigured
        with client.session_transaction() as sess:
            sess["user"] = {"id": 101, "name": "Daniel", "role": "super_admin"}
        with patch.object(workspace_board, "get_user", return_value=self.daniel), \
             patch.object(server, "load_cases", side_effect=lambda: case_store.get_all_cases()), \
             patch.object(server, "_cf_ai", side_effect=RuntimeError("Workers AI offline")):
            res_err = client.get("/api/workspace/cases/case-ai-target/ai-insights?llm=1")
        self.assertEqual(res_err.status_code, 200)
        self.assertIsNone(res_err.get_json()["llm_advice"])
        self.assertIn("temporarily unavailable", res_err.get_json()["llm_error"])

        with client.session_transaction() as sess:
            sess["user"] = {"id": 101, "name": "Daniel", "role": "super_admin"}
        with patch.object(workspace_board, "get_user", return_value=self.daniel), \
             patch.object(server, "load_cases", side_effect=lambda: case_store.get_all_cases()), \
             patch.object(server, "_cf_ai", return_value="• Document when the coolant warning appeared") as generate:
            res_ok = client.get("/api/workspace/cases/case-ai-target/ai-insights?llm=1")
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.get_json()["llm_advice"], "• Document when the coolant warning appeared")
        context = json.loads(generate.call_args.args[0][1]["content"])
        self.assertIn("Driver confirmed visible coolant near the upper clamp.", context["recent_case_notes"])
        self.assertNotIn("suggested_shops", context)

    def test_legacy_proofs_become_invoices_without_changing_file_records(self):
        case = dict(self.target, workspace_attachments=[
            {"id": "receipt", "name": "receipt.pdf", "category": "proof", "proof_type": "receipt", "version_num": 2},
            {"id": "proof", "name": "old proof.pdf", "category": "proof"},
            {"id": "photo", "name": "photo.jpg", "category": "attachment"},
        ])
        with db.write_tx(self.data_dir) as conn:
            conn.execute("UPDATE cases SET data_json=? WHERE id=?", (json.dumps(case), case["id"]))
            db.sync_normalized_case_row(conn, case)
            conn.execute("UPDATE meta SET value='4' WHERE key='live_board_schema_version'")
            db._migrate_live_board_schema(conn)
            db._migrate_live_board_schema(conn)
            saved = json.loads(conn.execute("SELECT data_json FROM cases WHERE id=?", (case["id"],)).fetchone()["data_json"])
            types = {r["id"]: r["proof_type"] for r in conn.execute(
                "SELECT id,proof_type FROM case_attachments WHERE case_id=?", (case["id"],)
            )}
        self.assertEqual(types, {"receipt": "invoice", "proof": "invoice", "photo": ""})
        self.assertEqual(saved["workspace_attachments"], [
            {**case["workspace_attachments"][0], "proof_type": "invoice"},
            {**case["workspace_attachments"][1], "proof_type": "invoice"},
            case["workspace_attachments"][2],
        ])

    def test_ai_insights_ignores_test_dummy_and_noise_matches(self):
        with db.write_tx(self.data_dir) as conn:
            for c in (
                {
                    "id": "case-test-dummy",
                    "case_type": "maintenance_report",
                    "unit_number": "test",
                    "report_driver": "test",
                    "driver": "test",
                    "vehicle_type": "truck",
                    "issue_text": "test test test",
                    "description": "test",
                    "load_type": "JBS Load",
                    "status": "reported",
                    "reported_at": "2026-11-14T10:00:00+00:00",
                    "workspace_notes": [{"id": "nt1", "text": "test"}],
                },
                {
                    "id": "case-noise-1",
                    "case_type": "maintenance_report",
                    "unit_number": "910",
                    "vehicle_type": "truck",
                    "issue_text": "test test test",
                    "description": "testing only",
                    "load_type": "JBS Load",
                    "status": "open",
                    "reported_at": "2026-11-14T11:00:00+00:00",
                },
                {
                    "id": "case-hist-test-word",
                    "case_type": "maintenance_report",
                    "unit_number": "777",
                    "vehicle_type": "truck",
                    "issue_text": "Brake chamber replacement and pressure test",
                    "description": "Shop performed road test after brake repair",
                    "load_type": "JBS Load",
                    "shop_info": "Love's #999",
                    "amount": "550.00",
                    "status": "done",
                    "reported_at": "2026-11-01T10:00:00+00:00",
                    "closed_at": "2026-11-01T15:00:00+00:00",
                },
                {
                    "id": "case-spn-hist",
                    "case_type": "maintenance_report",
                    "unit_number": "555",
                    "vehicle_type": "truck",
                    "issue_text": "Check engine light SPN 3251 FMI 18 aftertreatment derate",
                    "shop_info": "Peterbilt Service",
                    "amount": "1450.00",
                    "status": "done",
                    "reported_at": "2026-11-02T10:00:00+00:00",
                    "closed_at": "2026-11-02T16:00:00+00:00",
                },
                {
                    "id": "case-spn-target",
                    "case_type": "maintenance_report",
                    "unit_number": "556",
                    "vehicle_type": "truck",
                    "issue_text": "Active fault SPN-3251 regen issue",
                    "status": "assigned",
                    "reported_at": "2026-11-14T12:00:00+00:00",
                },
            ):
                conn.execute(
                    "INSERT INTO cases(id,status,agent_id,data_json) VALUES(?,?,?,?)",
                    (c["id"], c["status"], 101, json.dumps(c)),
                )

        client = server.app.test_client()
        with patch.object(workspace_board, "get_user", return_value=self.daniel), \
             patch.object(server, "load_cases", side_effect=lambda: case_store.get_all_cases()):
            for dummy_id in ("case-test-dummy", "case-noise-1"):
                with client.session_transaction() as sess:
                    sess["user"] = {"id": 101, "name": "Daniel", "role": "super_admin"}
                res = client.get(f"/api/workspace/cases/{dummy_id}/ai-insights")
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertEqual(data["similar_cases"], [], f"{dummy_id} unexpectedly matched {data['similar_cases']}")
                self.assertEqual(data["suggested_shops"], [])
                self.assertTrue(data["triage"].get("low_signal"))

            with client.session_transaction() as sess:
                sess["user"] = {"id": 101, "name": "Daniel", "role": "super_admin"}
            spn_res = client.get("/api/workspace/cases/case-spn-target/ai-insights")
            self.assertEqual(spn_res.status_code, 200)
            spn_data = spn_res.get_json()
            spn_ids = [s["id"] for s in spn_data["similar_cases"]]
            self.assertEqual(spn_ids, ["case-spn-hist"])
            self.assertIn("SPN3251", spn_data["triage"]["detected_codes"])


if __name__ == "__main__":
    unittest.main()
