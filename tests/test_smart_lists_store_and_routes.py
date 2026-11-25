"""Unit tests for persistent Smart Lists routes on the bot backend."""
import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from backend.storage import smart_lists_store


class SmartListsBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(gc.collect)
        self.addCleanup(self.temp.cleanup)
        db_file = str(Path(self.temp.name) / "smart-lists.sqlite3")
        p_env = patch.dict(os.environ, {"KURTEX_LISTS_DB": db_file})
        p_user = patch(
            "backend.storage.user_store.get_user",
            return_value={"id": 101, "name": "Daniel", "role": "developer"},
        )
        p_env.start()
        p_user.start()
        self.addCleanup(p_env.stop)
        self.addCleanup(p_user.stop)
        self.client = server.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = {"id": 101, "name": "Daniel", "role": "developer"}
            sess["workspace_csrf"] = "test-csrf-token"

    def test_agent_can_add_edit_and_delete_records_but_cannot_manage_lists_or_audit(self):
        headers={"X-Workspace-CSRF":"test-csrf-token"}
        with patch("backend.storage.user_store.get_user",return_value={"id":101,"name":"Agent","role":"agent"}):
            self.assertEqual(self.client.post("/api/smart-lists",headers=headers,json={"name":"test"}).status_code,403)
            self.assertEqual(self.client.get("/api/smart-lists/history").status_code,403)
            res=self.client.post("/api/smart-lists/equipment_campaigns/entries",headers=headers,json={"values":{"unit":"101"}})
            self.assertEqual(res.status_code,200)
            eid=res.get_json()["id"]
            self.assertEqual(self.client.put(f"/api/smart-lists/equipment_campaigns/entries/{eid}",headers=headers,json={"values":{"unit":"102"}}).status_code,200)
            self.assertEqual(self.client.delete(f"/api/smart-lists/equipment_campaigns/entries/{eid}",headers=headers).status_code,200)
            self.assertEqual(self.client.get("/api/smart-lists/equipment_campaigns").get_json()["entries"], [])
            self.assertEqual(self.client.delete("/api/smart-lists/equipment_campaigns",headers=headers).status_code,403)
        history=self.client.get("/api/smart-lists/history").get_json()["events"]
        self.assertTrue(any(event["action"]=="update" for event in history))
        self.assertTrue(any(event["action"]=="delete" and event["entry_id"]==eid for event in history))

    def test_smart_list_crud_persists_records(self):
        res = self.client.get("/api/smart-lists")
        self.assertEqual(res.status_code, 200)
        lists = res.get_json()["lists"]
        self.assertTrue(any(item["id"] == "equipment_campaigns" for item in lists))

        create_res = self.client.post(
            "/api/smart-lists/equipment_campaigns/entries",
            headers={"X-Workspace-CSRF": "test-csrf-token"},
            json={
                "values": {
                    "unit": "R391",
                    "equipment": "Truck",
                    "part": "Air Dryer",
                    "status": "In Progress",
                    "date": "2026-11-18",
                    "comment": "Scheduled inspection",
                }
            },
        )
        self.assertEqual(create_res.status_code, 200)
        entry_id = create_res.get_json()["id"]
        self.assertTrue(entry_id)

        get_res = self.client.get("/api/smart-lists/equipment_campaigns")
        self.assertEqual(get_res.status_code, 200)
        entries = get_res.get_json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["values"]["unit"], "R391")

        upd_res = self.client.put(
            f"/api/smart-lists/equipment_campaigns/entries/{entry_id}",
            headers={"X-Workspace-CSRF": "test-csrf-token"},
            json={"values": {"unit": "R391", "status": "Completed"}},
        )
        self.assertEqual(upd_res.status_code, 200)

        get_after = self.client.get("/api/smart-lists/equipment_campaigns")
        self.assertEqual(get_after.get_json()["entries"][0]["values"]["status"], "Completed")

        # Verify SQLite file retains data across store calls
        _, persisted = smart_lists_store.get_list_with_entries("equipment_campaigns")
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["values"]["status"], "Completed")

    def test_list_color_presets_and_custom_hex_round_trip(self):
        headers = {"X-Workspace-CSRF": "test-csrf-token"}
        path = "/api/smart-lists/equipment_campaigns"
        for color in ("default", "teal", "rose", "orange", "graphite", "#12ab34", "#ABCDEF"):
            with self.subTest(color=color):
                response = self.client.patch(path, headers=headers, json={"color": color})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["list"]["color"], color)
                self.assertEqual(self.client.get(path).get_json()["list"]["color"], color)
                lists = self.client.get("/api/smart-lists").get_json()["lists"]
                self.assertEqual(next(item for item in lists if item["id"] == "equipment_campaigns")["color"], color)
        for color in ("#123", "#zzzzzz", "red; display:none", None, 123):
            with self.subTest(invalid=color):
                self.assertEqual(self.client.patch(path, headers=headers, json={"color": color}).status_code, 400)
        self.assertEqual(self.client.get(path).get_json()["list"]["color"], "#ABCDEF")


if __name__ == "__main__":
    unittest.main()
