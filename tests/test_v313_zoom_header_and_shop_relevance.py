"""Live Board v313 tests: workspace zoom prefs & contextual shop/AI relevance filtering."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.storage import case_store, db, preference_store


class LiveBoardV313ZoomAndShopRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.patch_case_dir = patch.object(case_store, "_storage_dir", return_value=self.data_dir)
        self.patch_pref_dir = patch.object(preference_store, "_storage_dir", return_value=self.data_dir)
        self.patch_server_dir = patch.object(server, "DATA_DIR", self.data_dir)
        for p in (self.patch_case_dir, self.patch_pref_dir, self.patch_server_dir):
            p.start()
            self.addCleanup(p.stop)

        self.agent_a = {"id": 101, "name": "Andrei", "role": "agent", "username": "andrei"}
        self.agent_b = {"id": 102, "name": "Daniel", "role": "super_admin", "username": "daniel"}
        with db.write_tx(self.data_dir) as conn:
            for u in (self.agent_a, self.agent_b):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (u["id"], u["name"], u["username"], u["role"]),
                )

            cases = [
                {
                    "id": "case-r176-hist",
                    "case_type": "maintenance_report",
                    "unit_number": "R176",
                    "vehicle_type": "reefer",
                    "issue_text": "Autoinflation hose leak + no brakes at all",
                    "description": "Reefer trailer air line and brake chambers repaired",
                    "shop_info": "Speedco at Love's Travel Stop #563 - 66320 Belmont Morristown Rd Belmont, OH",
                    "amount": "340.00",
                    "status": "done",
                    "pickup": "Columbus, OH",
                    "delivery": "Pittsburgh, PA",
                    "reported_at": "2026-11-10T10:00:00+00:00",
                    "closed_at": "2026-11-10T15:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Andrei",
                },
                {
                    "id": "case-308-coolant",
                    "case_type": "maintenance_report",
                    "unit_number": "308",
                    "vehicle_type": "truck",
                    "issue_text": "Coolant hose leak near upper thermostat",
                    "description": "Tractor losing antifreeze slowly at clamp",
                    "pickup": "Dallas, TX",
                    "delivery": "Memphis, TN",
                    "status": "assigned",
                    "reported_at": "2026-11-14T08:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Andrei",
                },
                {
                    "id": "case-415-def",
                    "case_type": "maintenance_report",
                    "unit_number": "415",
                    "vehicle_type": "truck",
                    "issue_text": "Check engine light SPN 3251 derate",
                    "pickup": "Chicago, IL",
                    "delivery": "Atlanta, GA",
                    "status": "open",
                    "reported_at": "2026-11-14T09:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Andrei",
                },
                {
                    "id": "case-r190-oh-brake",
                    "case_type": "maintenance_report",
                    "unit_number": "R190",
                    "vehicle_type": "reefer",
                    "issue_text": "Trailer brakes dragging on rear tandem",
                    "pickup": "Wheeling, WV",
                    "delivery": "Belmont, OH",
                    "status": "assigned",
                    "reported_at": "2026-11-14T10:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Andrei",
                },
                {
                    "id": "case-r176-repeat",
                    "case_type": "maintenance_report",
                    "unit_number": "R176",
                    "vehicle_type": "reefer",
                    "issue_text": "Reefer unit rear marker light out",
                    "pickup": "Gary, IN",
                    "delivery": "Detroit, MI",
                    "status": "assigned",
                    "reported_at": "2026-11-14T11:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Andrei",
                },
            ]
            for c in cases:
                conn.execute(
                    "INSERT INTO cases(id,status,agent_id,data_json) VALUES(?,?,?,?)",
                    (c["id"], c["status"], c["agent_id"], json.dumps(c)),
                )

    def test_board_zoom_preference_per_agent_clamping_and_reset(self):
        client_a = server.app.test_client()
        client_b = server.app.test_client()
        with client_a.session_transaction() as sess:
            sess["user"] = {"id": 101, "name": "Andrei", "role": "agent"}
            sess["workspace_csrf"] = "csrf-a"
        with client_b.session_transaction() as sess:
            sess["user"] = {"id": 102, "name": "Daniel", "role": "super_admin"}
            sess["workspace_csrf"] = "csrf-b"

        with patch.object(workspace_board, "get_user", side_effect=lambda uid: self.agent_a if str(uid) == "101" else self.agent_b):
            get_default = client_a.get("/api/workspace/preferences")
            self.assertEqual(get_default.status_code, 200)
            self.assertEqual(get_default.get_json()["preferences"]["board_zoom"], 100)
            self.assertTrue(get_default.get_json()["preferences"]["toolbar_visibility"]["zoom"])

            put_zoom = client_a.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "csrf-a"},
                json={"board_zoom": 85},
            )
            self.assertEqual(put_zoom.status_code, 200)
            self.assertEqual(put_zoom.get_json()["preferences"]["board_zoom"], 85)

            # Agent B keeps independent default zoom
            get_b = client_b.get("/api/workspace/preferences")
            self.assertEqual(get_b.get_json()["preferences"]["board_zoom"], 100)

            # Bounds clamping (70..130)
            low = client_a.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "csrf-a"},
                json={"board_zoom": 10},
            )
            self.assertEqual(low.get_json()["preferences"]["board_zoom"], 70)
            high = client_a.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "csrf-a"},
                json={"board_zoom": 250},
            )
            self.assertEqual(high.get_json()["preferences"]["board_zoom"], 130)

            reset_res = client_a.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "csrf-a"},
                json={"reset": True},
            )
            self.assertEqual(reset_res.get_json()["preferences"]["board_zoom"], 100)

    def test_unrelated_r176_shop_never_suggested_by_default_in_shops_or_ai_insights(self):
        client = server.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 101, "name": "Andrei", "role": "agent"}

        with patch.object(workspace_board, "get_user", return_value=self.agent_a), \
             patch.object(server, "load_cases", side_effect=lambda: case_store.get_all_cases()):
            # 1. Truck coolant hose leak ("hose" + "leak") in TX->TN must NOT get Reefer Speedco R176 suggested
            shops_308 = client.get("/api/workspace/shops?case_id=case-308-coolant&context_only=1")
            self.assertEqual(shops_308.status_code, 200)
            self.assertEqual(shops_308.get_json()["shops"], [])

            ai_308 = client.get("/api/workspace/cases/case-308-coolant/ai-insights?preload=1")
            self.assertEqual(ai_308.status_code, 200)
            ai_body = ai_308.get_json()
            self.assertEqual(ai_body["similar_cases"], [])
            self.assertEqual(ai_body["suggested_shops"], [])

            # 2. Truck DEF derate in IL->GA must NOT get Reefer Speedco R176
            shops_415 = client.get("/api/workspace/shops?case_id=case-415-def&context_only=1")
            self.assertEqual(shops_415.status_code, 200)
            self.assertEqual(shops_415.get_json()["shops"], [])

            # 3. Reefer brakes in Belmont, OH DOES match Speedco #563 by route state OH + brakes system
            shops_r190 = client.get("/api/workspace/shops?case_id=case-r190-oh-brake&context_only=1")
            self.assertEqual(shops_r190.status_code, 200)
            r190_list = shops_r190.get_json()["shops"]
            self.assertEqual(len(r190_list), 1)
            self.assertIn("Speedco", r190_list[0]["shop_name"])
            self.assertGreaterEqual(r190_list[0]["relevance_score"], 5)
            self.assertIn("Route state OH", r190_list[0]["match_reasons"])
            self.assertEqual(r190_list[0]["matched_unit"], "")

            # 4. Same unit R176 DOES match Speedco #563 with Serviced Unit R176 reason
            shops_r176 = client.get("/api/workspace/shops?case_id=case-r176-repeat&context_only=1")
            self.assertEqual(shops_r176.status_code, 200)
            r176_list = shops_r176.get_json()["shops"]
            self.assertEqual(len(r176_list), 1)
            self.assertEqual(r176_list[0]["matched_unit"], "R176")
            self.assertIn("Serviced Unit R176", r176_list[0]["match_reasons"])

            # 5. Explicit user search (?q=speedco) still finds the shop even inside case 308
            search_308 = client.get("/api/workspace/shops?case_id=case-308-coolant&q=speedco")
            self.assertEqual(search_308.status_code, 200)
            self.assertEqual(len(search_308.get_json()["shops"]), 1)

            # 6. Global catalog without case_id preserves directory behavior with enriched state/records metadata
            global_res = client.get("/api/workspace/shops")
            self.assertEqual(global_res.status_code, 200)
            g_shops = global_res.get_json()["shops"]
            self.assertEqual(len(g_shops), 1)
            self.assertIn("OH", g_shops[0]["states"])
            self.assertTrue(len(g_shops[0]["records"]) >= 1)


if __name__ == "__main__":
    unittest.main()
