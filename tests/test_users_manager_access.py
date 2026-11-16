"""Tests for manager access to the Users & Permissions management tab."""
import gc
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from backend.storage import db, user_store


class UsersManagerAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(gc.collect)
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        for p in (
            patch.object(server, "DATA_DIR", self.data_dir),
            patch.object(user_store, "_storage_dir", return_value=self.data_dir),
        ):
            p.start()
            self.addCleanup(p.stop)

        with db.write_tx(self.data_dir) as conn:
            for uid, name, uname, role in (
                (100, "Daniel", "danieldev", "developer"),
                (200, "Max", "maxmgr", "super_admin"),
                (300, "Alex", "alexagent", "agent"),
            ):
                conn.execute(
                    "INSERT INTO users(id, name, username, role) VALUES(?,?,?,?)",
                    (uid, name, uname, role),
                )
            # Simulate an older deployment DB without 'users' seeded yet
            conn.execute(
                "INSERT INTO role_permissions(role, pages_json) VALUES('super_admin', ?)",
                (json.dumps(["overview", "cases", "agents", "shifts"]),),
            )
            conn.execute("INSERT INTO meta(key, value) VALUES('shifts_perm_seeded', '1')")
            conn.execute("INSERT INTO meta(key, value) VALUES('bot_issues_perm_seeded', '1')")

        user_store._invalidate_cache(self.data_dir)
        self.client = server.app.test_client()

    def _login(self, uid, name, role):
        with self.client.session_transaction() as sess:
            sess["user"] = {"id": uid, "name": name, "role": role}

    def test_manager_gets_users_page_via_seed_and_bootstrap(self):
        self._login(200, "Max", "super_admin")
        res = self.client.get("/api/frontend/bootstrap")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["is_manager"])
        self.assertFalse(body["is_developer"])
        self.assertIn("users", body["allowed_pages"])

    def test_agent_blocked_from_users_and_permissions_apis(self):
        self._login(300, "Alex", "agent")
        self.assertEqual(self.client.get("/api/developer/users").status_code, 403)
        self.assertEqual(self.client.get("/api/developer/permissions").status_code, 403)
        self.assertEqual(
            self.client.post("/api/developer/users", json={"id": 400, "name": "New", "role": "agent"}).status_code,
            403,
        )

    def test_manager_can_manage_agents_but_cannot_escalate_to_developer(self):
        self._login(200, "Max", "super_admin")

        # Read directory
        r_list = self.client.get("/api/developer/users")
        self.assertEqual(r_list.status_code, 200)
        self.assertFalse(r_list.get_json()["can_manage_developers"])
        self.assertGreaterEqual(len(r_list.get_json()["users"]), 3)

        # Add agent -> OK
        r_add = self.client.post(
            "/api/developer/users",
            json={"id": 401, "name": "Sam Agent", "username": "@samagent", "role": "agent"},
        )
        self.assertEqual(r_add.status_code, 200)

        # Promote agent to manager -> OK
        r_edit = self.client.patch(
            "/api/developer/users/401",
            json={"name": "Sam Lead", "username": "samlead", "role": "super_admin"},
        )
        self.assertEqual(r_edit.status_code, 200)
        self.assertEqual(user_store.get_user(401)["role"], "super_admin")

        # Cannot create developer, promote to developer, edit developer, or delete developer
        self.assertEqual(
            self.client.post("/api/developer/users", json={"id": 402, "name": "Bad Dev", "role": "developer"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch("/api/developer/users/401", json={"name": "Sam", "role": "developer"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch("/api/developer/users/100", json={"name": "Hack", "role": "agent"}).status_code,
            403,
        )
        self.assertEqual(self.client.delete("/api/developer/users/100").status_code, 403)

        # Can remove non-developer user
        self.assertEqual(self.client.delete("/api/developer/users/401").status_code, 200)

        # Cannot edit super_admin permissions (server preserves them), CAN edit agent permissions
        r_perm = self.client.put(
            "/api/developer/permissions",
            json={
                "permissions": {
                    "agent": ["overview", "cases"],
                    "super_admin": ["overview"],  # Should be ignored for manager callers
                }
            },
        )
        self.assertEqual(r_perm.status_code, 200)
        perms = r_perm.get_json()["permissions"]
        self.assertEqual(perms["agent"], ["overview", "cases", "my_profile", "report_issue"])
        self.assertIn("users", perms["super_admin"])


if __name__ == "__main__":
    unittest.main()
