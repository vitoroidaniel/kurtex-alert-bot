"""Live Board v312 tests: per-agent column/preset persistence, watchlist, and live pickup/delivery shop search."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.ai import live_shop_search
from backend.storage import case_store, db, preference_store


class LiveBoardV312Tests(unittest.TestCase):
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
        self.victor = {"id": 102, "name": "Victor", "role": "agent", "username": "victors"}
        with db.write_tx(self.data_dir) as conn:
            for u in (self.daniel, self.victor):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (u["id"], u["name"], u["username"], u["role"]),
                )
            seed_cases = [
                {
                    "id": "case-route-1",
                    "case_type": "maintenance_report",
                    "unit_number": "701",
                    "vehicle_type": "truck",
                    "issue_text": "Steer tire blowout near Gary Indiana, need mobile tire service",
                    "shop_info": "TA Truck Service Gary",
                    "pickup": "Chicago, IL",
                    "delivery": "Dallas, TX",
                    "location": "Gary, IN",
                    "amount": "920.00",
                    "status": "reported",
                    "reported_at": "2026-11-12T10:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Daniel",
                },
                {
                    "id": "case-route-hist",
                    "case_type": "maintenance_report",
                    "unit_number": "702",
                    "vehicle_type": "truck",
                    "issue_text": "Drive tire blowout along I-80",
                    "shop_info": "TA Truck Service Gary",
                    "amount": "850.00",
                    "status": "done",
                    "reported_at": "2026-11-05T10:00:00+00:00",
                    "closed_at": "2026-11-05T14:00:00+00:00",
                    "agent_id": 101,
                    "agent_name": "Daniel",
                },
                {
                    "id": "case-no-route",
                    "case_type": "maintenance_report",
                    "unit_number": "703",
                    "vehicle_type": "trailer",
                    "issue_text": "ABS sensor light stays on",
                    "shop_info": "-",
                    "pickup": "-",
                    "delivery": "",
                    "location": "N/A",
                    "status": "reported",
                    "reported_at": "2026-11-12T11:00:00+00:00",
                    "agent_id": 102,
                    "agent_name": "Victor",
                },
            ]
            for row in seed_cases:
                conn.execute(
                    "INSERT INTO cases(id,status,agent_id,data_json) VALUES(?,?,?,?)",
                    (row["id"], row["status"], row["agent_id"], json.dumps(row)),
                )

    def _auth_session(self, client, user):
        with client.session_transaction() as sess:
            sess["user"] = {"id": user["id"], "name": user["name"], "role": user["role"]}
            sess["workspace_csrf"] = "test-csrf"

    def test_per_agent_individual_column_and_preset_persistence(self):
        client_d = server.app.test_client()
        self._auth_session(client_d, self.daniel)
        with patch.object(workspace_board, "get_user", return_value=self.daniel), patch.object(
            workspace_board, "get_all_user_dicts", return_value=[self.daniel, self.victor]
        ):
            res_put = client_d.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={
                    "hide_off_shift": False,
                    "compact": True,
                    "board_view": "grid",
                    "collapsed_lanes": ["agent:102", "stage:jbs_plant", "agent:102"],
                    "expanded_lanes": ["agent:105", "agent:102"],  # agent:102 kept only in collapsed_lanes
                    "watchlist_ids": ["case-route-1", "case-route-1"],
                    "toolbar_visibility": {"presets": True, "watchlist": False, "shift_summary": True},
                    "saved_views": [
                        {
                            "id": "v-pinned",
                            "name": "Watch High",
                            "pin_to_toolbar": True,
                            "filters": {
                                "priority": "high",
                                "watchlist_only": True,
                                "hide_off_shift": False,
                                "view": "grid",
                                "compact": True,
                            },
                        }
                    ],
                    "active_view_id": "v-pinned",
                },
            )
            self.assertEqual(res_put.status_code, 200)
            prefs_d = res_put.get_json()["preferences"]
            self.assertFalse(prefs_d["hide_off_shift"])
            self.assertTrue(prefs_d["compact"])
            self.assertEqual(prefs_d["board_view"], "grid")
            self.assertEqual(prefs_d["collapsed_lanes"], ["agent:102", "stage:jbs_plant"])
            self.assertEqual(prefs_d["expanded_lanes"], ["agent:105"])
            self.assertEqual(prefs_d["watchlist_ids"], ["case-route-1"])
            self.assertTrue(prefs_d["toolbar_visibility"]["presets"])
            self.assertFalse(prefs_d["toolbar_visibility"]["watchlist"])
            self.assertTrue(prefs_d["saved_views"][0]["pin_to_toolbar"])
            self.assertTrue(prefs_d["saved_views"][0]["filters"]["watchlist_only"])

        # Simulate another agent logging in: must NOT inherit Daniel's layout or watchlist
        client_v = server.app.test_client()
        self._auth_session(client_v, self.victor)
        with patch.object(workspace_board, "get_user", return_value=self.victor), patch.object(
            workspace_board, "get_all_user_dicts", return_value=[self.daniel, self.victor]
        ):
            res_v = client_v.get("/api/workspace/agents")
            self.assertEqual(res_v.status_code, 200)
            prefs_v = res_v.get_json()["workspace_prefs"]
            self.assertTrue(prefs_v["hide_off_shift"])
            self.assertFalse(prefs_v["compact"])
            self.assertEqual(prefs_v["board_view"], "board")
            self.assertEqual(prefs_v["collapsed_lanes"], [])
            self.assertEqual(prefs_v["expanded_lanes"], [])
            self.assertEqual(prefs_v["watchlist_ids"], [])
            self.assertEqual(prefs_v["saved_views"], [])

            # Victor saves his own distinct column collapse state
            res_v_put = client_v.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "test-csrf"},
                json={"collapsed_lanes": ["unassigned"], "watchlist_ids": ["case-no-route"]},
            )
            self.assertEqual(res_v_put.status_code, 200)

        # Simulate Daniel returning in a fresh client after logout/login
        fresh_d = server.app.test_client()
        self._auth_session(fresh_d, self.daniel)
        with patch.object(workspace_board, "get_user", return_value=self.daniel), patch.object(
            workspace_board, "get_all_user_dicts", return_value=[self.daniel, self.victor]
        ):
            res_back = fresh_d.get("/api/workspace/agents")
            self.assertEqual(res_back.status_code, 200)
            restored = res_back.get_json()["workspace_prefs"]
            self.assertEqual(restored["collapsed_lanes"], ["agent:102", "stage:jbs_plant"])
            self.assertEqual(restored["expanded_lanes"], ["agent:105"])
            self.assertEqual(restored["watchlist_ids"], ["case-route-1"])
            self.assertFalse(restored["hide_off_shift"])
            self.assertEqual(restored["board_view"], "grid")

    def test_live_shop_search_no_route_returns_zero_fake_shops(self):
        called = []

        def fail_if_called(*args, **kwargs):
            called.append(args)
            raise AssertionError("Should not make external HTTP calls for blank pickup/delivery")

        out = live_shop_search.search_live_repair_shops(
            pickup="-",
            delivery="N/A",
            location="",
            http_fetch=fail_if_called,
        )
        self.assertEqual(out["status"], "no_route")
        self.assertEqual(out["shops"], [])
        self.assertEqual(called, [])

    def test_live_shop_search_osm_pickup_and_delivery(self):
        def fake_http(url, *, method="GET", headers=None, data=None, timeout=6.0):
            if "nominatim.openstreetmap.org" in url:
                if "Chicago" in url:
                    return [{"lat": "41.8781", "lon": "-87.6298", "display_name": "Chicago, Cook County, Illinois"}]
                if "Dallas" in url:
                    return [{"lat": "32.7767", "lon": "-96.7970", "display_name": "Dallas, Dallas County, Texas"}]
                return []
            if "overpass" in url:
                return {
                    "elements": [
                        {
                            "type": "node",
                            "lat": 41.8400,
                            "lon": -87.6100,
                            "tags": {
                                "name": "Midwest Heavy Truck & Tire",
                                "shop": "truck_repair",
                                "hgv": "yes",
                                "addr:housenumber": "1200",
                                "addr:street": "W 35th St",
                                "addr:city": "Chicago",
                                "addr:state": "IL",
                                "phone": "+1-312-555-0140",
                                "opening_hours": "24/7",
                            },
                        },
                        {
                            "type": "way",
                            "center": {"lat": 37.3300, "lon": -92.2100},
                            "tags": {
                                "name": "I-44 Route Truck & Trailer Center",
                                "amenity": "truck_repair",
                                "addr:city": "Lebanon",
                                "addr:state": "MO",
                                "phone": "+1-417-555-0199",
                            },
                        },
                        {
                            "type": "node",
                            "lat": 32.7900,
                            "lon": -96.8100,
                            "tags": {
                                "name": "Jiffy Lube Quick Oil",
                                "shop": "car_repair",
                            },
                        },
                    ]
                }
            raise AssertionError(f"Unexpected URL {url}")

        with patch.dict(os.environ, {"SERPER_API_KEY": ""}, clear=False):
            out = live_shop_search.search_live_repair_shops(
                pickup="Chicago, IL",
                delivery="Dallas, TX",
                vehicle_type="truck",
                systems={"tires"},
                http_fetch=fake_http,
            )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["provider"], "OpenStreetMap")
        names = [s["shop"] for s in out["shops"]]
        self.assertIn("Midwest Heavy Truck & Tire", names)
        self.assertIn("I-44 Route Truck & Trailer Center", names)
        self.assertNotIn("Jiffy Lube Quick Oil", names)
        chicago_shop = next(s for s in out["shops"] if s["shop"] == "Midwest Heavy Truck & Tire")
        self.assertEqual(chicago_shop["anchor_key"], "pickup")
        self.assertIsNotNone(chicago_shop["distance_miles"])
        self.assertLess(chicago_shop["distance_miles"], 10.0)
        self.assertEqual(chicago_shop["phone"], "+1-312-555-0140")
        self.assertEqual(chicago_shop["opening_hours"], "24/7")
        mid_shop = next(s for s in out["shops"] if s["shop"] == "I-44 Route Truck & Trailer Center")
        self.assertEqual(mid_shop["anchor_key"], "corridor")

    def test_live_shop_search_unavailable_never_fabricates_data(self):
        def down_http(*args, **kwargs):
            raise TimeoutError("offline")

        with patch.dict(os.environ, {"SERPER_API_KEY": "fake-key"}, clear=False):
            out = live_shop_search.search_live_repair_shops(
                pickup="Chicago, IL",
                delivery="Dallas, TX",
                http_fetch=down_http,
            )
        self.assertEqual(out["status"], "unavailable")
        self.assertEqual(out["shops"], [])

    def test_ai_insights_never_looks_up_route_shops(self):
        client = server.app.test_client()
        self._auth_session(client, self.daniel)
        with patch.object(workspace_board, "get_user", return_value=self.daniel), patch.object(
            server, "_search_live_route_shops"
        ) as search:
            for query in ("", "?preload=1", "?force_live=1"):
                res = client.get("/api/workspace/cases/case-route-1/ai-insights" + query)
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.get_json()["suggested_shops"], [])
                self.assertTrue(res.get_json()["triage"]["troubleshooting_steps"])
            search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
