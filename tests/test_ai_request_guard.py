import unittest
from unittest.mock import patch
from backend.ai.request_guard import UserOperations, UserRateLimit
from api import server


class AIRequestGuardTests(unittest.TestCase):
    def test_independent_users_and_idempotent_release(self):
        registry = UserOperations()
        first = registry.acquire("one")
        second = registry.acquire("two")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(registry.acquire("one"))
        first()
        next_operation = registry.acquire("one")
        first()
        self.assertIsNone(registry.acquire("one"))
        next_operation()
        second()

    def test_rate_limit_expires_and_remains_bounded(self):
        now = [10.0]
        limit = UserRateLimit(limit=2, window=60, max_users=2, clock=lambda: now[0])
        self.assertTrue(limit.allow("one"))
        self.assertTrue(limit.allow("one"))
        self.assertFalse(limit.allow("one"))
        self.assertTrue(limit.allow("two"))
        self.assertFalse(limit.allow("three"))
        now[0] += 61
        self.assertTrue(limit.allow("three"))
        self.assertTrue(limit.allow("one"))

    def test_revoked_and_disabled_ai_permissions(self):
        client = server.app.test_client()
        self.assertEqual(client.get("/api/ai/chats").status_code, 401)
        with client.session_transaction() as sess:
            sess["user"] = {"id": 901, "role": "developer"}
        with patch("backend.storage.user_store.get_user", return_value=None):
            self.assertEqual(client.get("/api/ai/chats").status_code, 403)
        with patch("backend.storage.user_store.get_user", return_value={"role":"agent"}), \
             patch.object(server, "_load_role_permissions", return_value={"agent":["cases"]}):
            self.assertEqual(client.get("/api/ai/chats").status_code, 403)

    def test_trainer_role_rechecked_from_store(self):
        client = server.app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = {"id": 902, "role": "developer"}
        with patch("backend.storage.user_store.get_user", return_value={"role":"agent"}), \
             patch.object(server, "_load_role_permissions", return_value={"agent":["ai_assistant"]}):
            self.assertIn(client.post("/api/ai/test").status_code, (401, 403))
