"""Live Board v311 tests: workspace board preferences, saved views persistence, and shop autocomplete directory."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.storage import case_store, db, preference_store


class LiveBoardV311PrefsAndShopsTests(unittest.TestCase):
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
        with db.write_tx(self.data_dir) as conn:
            conn.execute(
                "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                (self.daniel["id"], self.daniel["name"], self.daniel["username"], self.daniel["role"]),
            )
            seed_cases = [
                {
                    "id": "case-shop-1",
                    "case_type": "maintenance_report",
                    "unit_number": "308",
                    "vehicle_type": "truck",
                    "issue_text": "Coolant leak at thermostat",
                    "shop_info": "TA Truck Service #142 Gary IN",
                    "amount": "640.00",
                    "status": "done",
                    "reported_at": "2026-11-05T10:00:00+00:00",
                    "closed_at": "2026-11-05T16:30:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Daniel",
                },
                {
                    "id": "case-shop-2",
                    "case_type": "maintenance_report",
                    "unit_number": "419",
                    "vehicle_type": "reefer",
                    "issue_text": "Radiator hose clamp",
                    "shop_info": "TA Truck Service #142 Gary IN",
                    "amount": "860.00",
                    "status": "done",
                    "reported_at": "2026-11-08T10:00:00+00:00",
                    "closed_at": "2026-11-08T15:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Daniel",
                },
                {
                    "id": "case-shop-3",
                    "case_type": "maintenance_report",
                    "unit_number": "512",
                    "vehicle_type": "trailer",
                    "issue_text": "Dual steer tire blowout",
                    "shop_info": "Speedco #882 Indianapolis",
                    "amount": "450.00",
                    "status": "reported",
                    "reported_at": "2026-11-12T10:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Daniel",
                },
                {
                    "id": "case-shop-ignored",
                    "case_type": "maintenance_report",
                    "unit_number": "600",
                    "vehicle_type": "truck",
                    "issue_text": "Minor lamp check",
                    "shop_info": "N/A",
                    "status": "done",
                    "reported_at": "2026-11-11T10:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Daniel",
                },
                {
                    "id": "case-shop-test",
                    "case_type": "maintenance_report",
                    "unit_number": "test",
                    "vehicle_type": "truck",
                    "issue_text": "Fake test shop case",
                    "shop_info": "Secret Fake Test Garage #999",
                    "is_test": True,
                    "status": "done",
                    "reported_at": "2026-11-12T11:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Daniel",
                },
            ]
            for c in seed_cases:
                conn.execute(
                    "INSERT INTO cases(id,status,agent_id,data_json) VALUES(?,?,?,?)",
                    (c["id"], c["status"], c["agent_id"], json.dumps(c)),
                )

    def _auth_session(self, client, user):
        with client.session_transaction() as sess:
            sess["user"] = {"id": user["id"], "name": user["name"], "role": user["role"]}
            sess["workspace_csrf"] = "test-csrf"

    def test_workspace_preferences_persistence_saved_views_and_reset(self):
        client = server.app.test_client()
        self._auth_session(client, self.daniel)
        with patch.object(workspace_board, "get_user", return_value=self.daniel), \
             patch.object(workspace_board, "get_all_user_dicts", return_value=[self.daniel]):
            res_get = client.get("/api/workspace/preferences")
            self.assertEqual(res_get.status_code, 200)
            prefs = res_get.get_json()["preferences"]
            self.assertTrue(prefs["background_alerts"])
            self.assertTrue(prefs["alert_sound"])
            self.assertTrue(prefs["ai_autocomplete"])
            self.assertTrue(prefs["ai_tab_badge"])
            self.assertTrue(prefs["toolbar_visibility"]["export_csv"])
            self.assertEqual(prefs["saved_views"], [])

            res_put = client.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={
                    "background_alerts": False,
                    "ai_autocomplete": False,
                    "toolbar_visibility": {"export_csv": False, "compact": False},
                    "active_view_id": "view-1",
                    "saved_views": [
                        {
                            "id": "view-1",
                            "name": "My High Shop Cases",
                            "filters": {
                                "priority": "high",
                                "my_cases": True,
                                "has_shop": True,
                                "view": "grid",
                                "compact": True,
                            },
                        },
                        {"id": "", "name": "Invalid Blank Id", "filters": {}},
                    ],
                },
            )
            self.assertEqual(res_put.status_code, 200)
            updated = res_put.get_json()["preferences"]
            self.assertFalse(updated["background_alerts"])
            self.assertTrue(updated["alert_sound"])
            self.assertFalse(updated["ai_autocomplete"])
            self.assertTrue(updated["ai_tab_badge"])
            self.assertFalse(updated["toolbar_visibility"]["export_csv"])
            self.assertFalse(updated["toolbar_visibility"]["compact"])
            self.assertTrue(updated["toolbar_visibility"]["priorities"])
            self.assertEqual(len(updated["saved_views"]), 1)
            self.assertEqual(updated["saved_views"][0]["filters"]["view"], "grid")
            self.assertEqual(updated["active_view_id"], "view-1")

            # Verify /api/workspace/agents hydrates workspace_prefs too
            res_agents = client.get("/api/workspace/agents")
            self.assertEqual(res_agents.status_code, 200)
            agent_prefs = res_agents.get_json()["workspace_prefs"]
            self.assertFalse(agent_prefs["background_alerts"])
            self.assertEqual(agent_prefs["active_view_id"], "view-1")

            # Reset restores defaults
            res_reset = client.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"reset": True},
            )
            self.assertEqual(res_reset.status_code, 200)
            restored = res_reset.get_json()["preferences"]
            self.assertTrue(restored["background_alerts"])
            self.assertTrue(restored["ai_autocomplete"])
            self.assertTrue(restored["toolbar_visibility"]["export_csv"])
            self.assertEqual(restored["saved_views"], [])

    def test_workspace_shops_directory_aggregates_and_excludes_test_cases(self):
        client = server.app.test_client()
        self._auth_session(client, self.daniel)
        with patch.object(workspace_board, "get_user", return_value=self.daniel):
            res = client.get("/api/workspace/shops")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            shops = data["shops"]
            names = [s["shop_name"] for s in shops]
            self.assertIn("TA Truck Service #142 Gary IN", names)
            self.assertIn("Speedco #882 Indianapolis", names)
            self.assertNotIn("Secret Fake Test Garage #999", names)
            self.assertNotIn("N/A", names)

            ta = next(s for s in shops if "TA Truck Service" in s["shop_name"])
            self.assertEqual(ta["count"], 2)
            self.assertEqual(ta["resolved_count"], 2)
            self.assertEqual(ta["avg_amount"], 750.0)
            self.assertIn("truck", ta["vehicle_types"])
            self.assertIn("reefer", ta["vehicle_types"])

            res_filtered = client.get("/api/workspace/shops?q=speedco")
            self.assertEqual(res_filtered.status_code, 200)
            f_shops = res_filtered.get_json()["shops"]
            self.assertEqual(len(f_shops), 1)
            self.assertEqual(f_shops[0]["shop_name"], "Speedco #882 Indianapolis")


if __name__ == "__main__":
    unittest.main()
