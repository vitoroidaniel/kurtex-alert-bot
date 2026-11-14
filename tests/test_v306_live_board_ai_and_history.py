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
                    "issue_text": "Radiator hose coolant leak",
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

    def test_ai_insights_aggregates_shops_costs_unit_history_and_llm_fallback(self):
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
        self.assertTrue(any(s["shop_name"] == "TA Truck Service #142" for s in body["suggested_shops"]))
        top_shop = body["suggested_shops"][0]
        self.assertEqual(top_shop["shop_name"], "TA Truck Service #142")
        self.assertEqual(top_shop["avg_amount"], 780.0)
        self.assertEqual(body["cost_estimate"]["min"], 640.0)
        self.assertEqual(body["cost_estimate"]["max"], 920.0)
        similar_ids = [s["id"] for s in body["similar_cases"]]
        self.assertIn("case-hist-1", similar_ids)
        self.assertIn("case-hist-2", similar_ids)
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
        self.assertIn("offline", res_err.get_json()["llm_error"])

        with client.session_transaction() as sess:
            sess["user"] = {"id": 101, "name": "Daniel", "role": "super_admin"}
        with patch.object(workspace_board, "get_user", return_value=self.daniel), \
             patch.object(server, "load_cases", side_effect=lambda: case_store.get_all_cases()), \
             patch.object(server, "_cf_ai", return_value="• Route to TA Truck Service #142"):
            res_ok = client.get("/api/workspace/cases/case-ai-target/ai-insights?llm=1")
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.get_json()["llm_advice"], "• Route to TA Truck Service #142")


if __name__ == "__main__":
    unittest.main()
