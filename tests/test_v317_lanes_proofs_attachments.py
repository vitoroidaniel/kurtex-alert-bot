"""Live Board v317 backend tests: custom lanes, per-user lane order, proofs hub & attachment versioning/actions."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.storage import case_store, db, preference_store


class LiveBoardV317BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        for p in (
            patch.object(case_store, "_storage_dir", return_value=self.data_dir),
            patch.object(preference_store, "_storage_dir", return_value=self.data_dir),
            patch.object(server, "DATA_DIR", self.data_dir),
        ):
            p.start()
            self.addCleanup(p.stop)

        self.dev = {"id": 100, "name": "Daniel", "username": "danieldev", "role": "developer"}
        self.agent = {"id": 101, "name": "Alex", "username": "alex", "role": "agent"}
        with db.write_tx(self.data_dir) as conn:
            for u in (self.dev, self.agent):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (u["id"], u["name"], u["username"], u["role"]),
                )
        self.case = case_store.workspace_write(
            self.dev,
            {
                "id": "case-v317-1",
                "request_id": "req-v317-1",
                "unit_number": "308",
                "vehicle_type": "truck",
                "driver": "Ben Sumner",
                "priority": "high",
                "issue": "Coolant leak near thermostat",
                "target_agent": {"id": 101, "name": "Alex", "username": "alex"},
            },
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

    def test_custom_lanes_lifecycle_and_per_user_lane_order(self):
        with self._auth_patch():
            # Add a custom lane
            res = self.client.post(
                "/api/workspace/lanes",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={"name": "Waiting Parts", "color": "#059669"},
            )
            self.assertEqual(res.status_code, 201)
            lane = res.get_json()["lane"]
            self.assertTrue(lane["id"].startswith("custom_"))
            self.assertEqual(lane["name"], "Waiting Parts")

            # Move case into custom stage
            mv = self.client.patch(
                f"/api/workspace/cases/{self.case['id']}",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={"action": "stage", "stage": lane["id"], "revision": case_store.workspace_revision(self.case)},
            )
            self.assertEqual(mv.status_code, 200)
            self.assertEqual(mv.get_json()["case"]["board_stage"], lane["id"])

            # Save per-user lane order
            ord_res = self.client.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={"lane_order": ["stage:" + lane["id"], "agent:101", "done"]},
            )
            self.assertEqual(ord_res.status_code, 200)
            self.assertEqual(
                ord_res.get_json()["preferences"]["lane_order"],
                ["stage:" + lane["id"], "agent:101", "done"],
            )

            # Delete custom lane re-homes case stage cleanly back to ""
            del_res = self.client.delete(
                f"/api/workspace/lanes/{lane['id']}",
                headers={"X-Workspace-CSRF": "csrf-1"},
            )
            self.assertEqual(del_res.status_code, 200)
            self.assertEqual(del_res.get_json()["custom_lanes"], [])
            refreshed = case_store.get_case(self.case["id"])
            self.assertEqual(refreshed.get("board_stage") or "", "")

    def test_attachments_convert_description_versioning_bulk_and_proofs_hub(self):
        with self._auth_patch(), patch.object(
            server,
            "load_cases",
            side_effect=lambda: [case_store.get_case(self.case["id"])] if case_store.get_case(self.case["id"]) else [],
        ):
            # Upload initial v1 attachment
            up = self.client.post(
                f"/api/workspace/cases/{self.case['id']}/attachments",
                headers={"X-Workspace-CSRF": "csrf-1"},
                data={"file": (io.BytesIO(b"version-1-bytes"), "invoice.pdf"), "category": "attachment"},
                content_type="multipart/form-data",
            )
            self.assertEqual(up.status_code, 200)
            att_id = up.get_json()["id"]

            # Convert to proof + edit description + rename
            patch_res = self.client.patch(
                f"/api/workspace/cases/{self.case['id']}/attachments/{att_id}",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={
                    "name": "Loves Invoice #401",
                    "description": "Paid radiator flush invoice",
                    "category": "proof",
                    "proof_type": "invoice",
                },
            )
            self.assertEqual(patch_res.status_code, 200)
            att = patch_res.get_json()["attachment"]
            self.assertEqual(att["name"], "Loves Invoice #401.pdf")
            self.assertEqual(att["category"], "proof")
            self.assertEqual(att["proof_type"], "invoice")
            self.assertEqual(att["description"], "Paid radiator flush invoice")

            # Upload new version v2
            v2 = self.client.post(
                f"/api/workspace/cases/{self.case['id']}/attachments/{att_id}/versions",
                headers={"X-Workspace-CSRF": "csrf-1"},
                data={"file": (io.BytesIO(b"version-2-bytes"), "invoice_revised.pdf")},
                content_type="multipart/form-data",
            )
            self.assertEqual(v2.status_code, 200)
            att2 = v2.get_json()["attachment"]
            self.assertEqual(att2["version_num"], 2)
            self.assertEqual(len(att2["versions"]), 2)

            # Serve v2 default & v1 historical bytes
            with self.client.get(f"/api/workspace/cases/{self.case['id']}/attachments/{att_id}") as latest_dl:
                self.assertEqual(latest_dl.status_code, 200)
                self.assertEqual(latest_dl.data, b"version-2-bytes")

            v1_id = att2["versions"][0]["id"]
            with self.client.get(f"/api/workspace/cases/{self.case['id']}/attachments/{att_id}/versions/{v1_id}") as v1_dl:
                self.assertEqual(v1_dl.status_code, 200)
                self.assertEqual(v1_dl.data, b"version-1-bytes")

            # Proofs hub lists it
            proofs_res = self.client.get("/api/workspace/proofs")
            self.assertEqual(proofs_res.status_code, 200)
            proofs = proofs_res.get_json()["proofs"]
            self.assertEqual(len(proofs), 1)
            self.assertEqual(proofs[0]["proof_type"], "invoice")

            # Bulk delete removes file & versions cleanly
            bulk_res = self.client.post(
                f"/api/workspace/cases/{self.case['id']}/attachments/bulk",
                headers={"X-Workspace-CSRF": "csrf-1"},
                json={"action": "delete", "ids": [att_id]},
            )
            self.assertEqual(bulk_res.status_code, 200)
            self.assertEqual(bulk_res.get_json()["processed"], [att_id])
            self.assertEqual(self.client.get("/api/workspace/proofs").get_json()["proofs"], [])

    def test_existing_db_migrates_attachment_version_columns_on_upload(self):
        # Simulate a pre-v317 SQLite DB where case_attachments lacked version_num/versions_json
        # while .kurtex-db-initialized and live_board_normalized_v1='1' already exist.
        with db.write_tx(self.data_dir) as conn:
            conn.execute("DROP TABLE case_attachments")
            conn.execute(
                """CREATE TABLE case_attachments (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    extension TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'attachment',
                    proof_type TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    uploaded_by TEXT NOT NULL DEFAULT '',
                    uploaded_by_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                )"""
            )
            conn.execute("DELETE FROM meta WHERE key='live_board_schema_version'")
        db._INITIALIZED_DBS.clear()

        with self._auth_patch():
            up = self.client.post(
                f"/api/workspace/cases/{self.case['id']}/attachments",
                headers={"X-Workspace-CSRF": "csrf-1"},
                data={"file": (io.BytesIO(b"\xff\xd8\xff\xe0"), "22. Windows Logo.jpg"), "category": "attachment"},
                content_type="multipart/form-data",
            )
            self.assertEqual(up.status_code, 200, up.get_json())
            with db.read_conn(self.data_dir) as conn:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(case_attachments)")}
                self.assertIn("version_num", cols)
                self.assertIn("versions_json", cols)


if __name__ == "__main__":
    unittest.main()
