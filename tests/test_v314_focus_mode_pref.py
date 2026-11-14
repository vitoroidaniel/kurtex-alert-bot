"""Live Board v314 tests: per-user focus_mode preference persistence & reset."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from api.routes import workspace_board
from backend.storage import db, preference_store


class LiveBoardV314FocusModePrefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.patch_pref_dir = patch.object(preference_store, "_storage_dir", return_value=self.data_dir)
        self.patch_server_dir = patch.object(server, "DATA_DIR", self.data_dir)
        for p in (self.patch_pref_dir, self.patch_server_dir):
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

    def test_focus_mode_defaults_persists_isolates_and_resets(self):
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
            self.assertIs(get_default.get_json()["preferences"]["focus_mode"], False)

            put_on = client_a.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "csrf-a"},
                json={"focus_mode": True},
            )
            self.assertEqual(put_on.status_code, 200)
            self.assertIs(put_on.get_json()["preferences"]["focus_mode"], True)

            # Second user keeps independent False default
            get_b = client_b.get("/api/workspace/preferences")
            self.assertEqual(get_b.status_code, 200)
            self.assertIs(get_b.get_json()["preferences"]["focus_mode"], False)

            # Reset restores focus_mode to False for user A
            reset_a = client_a.put(
                "/api/workspace/preferences",
                headers={"X-Workspace-CSRF": "csrf-a"},
                json={"reset": True},
            )
            self.assertEqual(reset_a.status_code, 200)
            self.assertIs(reset_a.get_json()["preferences"]["focus_mode"], False)


if __name__ == "__main__":
    unittest.main()
