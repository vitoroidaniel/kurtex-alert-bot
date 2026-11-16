"""Tests for fast, gated AI intent routing and SSE streaming on /api/ai/chat."""
import gc
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import server
from backend.ai.ai_intent import classify_task, needs_web_search
from backend.ai.ai_chat_store import ChatStore


class AiChatStreamingAndIntentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(gc.collect)
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.chat_store = ChatStore(self.data_dir)
        p_store = patch.object(server, "chat_store", self.chat_store)
        p_cases = patch.object(server, "load_cases", return_value=[])
        p_learn = patch.object(server.learning_store, "matches", return_value=[])
        p_fleet = patch.object(server.fleet_knowledge_store, "matches", return_value=[])
        p_learn_cap = patch.object(server.learning_store, "capture_chat", return_value=None)
        for p in (p_store, p_cases, p_learn, p_fleet, p_learn_cap):
            p.start()
            self.addCleanup(p.stop)
        self.client = server.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = {"id": 101, "name": "Daniel", "role": "developer"}

    def test_board_ops_and_web_gating(self):
        msg = "Summarize active breakdowns\n\n[Live Board Summary]\nCurrent Live Board: 2 active cases"
        task = classify_task(msg)
        self.assertEqual(task, "board_ops")
        self.assertFalse(needs_web_search(msg, task))
        self.assertTrue(needs_web_search("Freightliner SPN 5246 FMI 0 check engine", "fault_code"))
        self.assertFalse(needs_web_search("ok thx", "maintenance_question"))

    def test_board_ops_json_skips_web_and_uses_compact_budget(self):
        calls = []

        def fake_cf(messages, max_tokens=900, temperature=0.2, **_kw):
            calls.append({"messages": messages, "max_tokens": max_tokens})
            return "2 active cases: Unit #308 (High, Dealer / Shop)."

        with patch.object(server, "_cf_ai", side_effect=fake_cf), patch.object(
            server, "_ai_web_search"
        ) as mock_web:
            res = self.client.post(
                "/api/ai/chat",
                json={
                    "message": "Which units are high priority?\n\n[Live Board Summary]\nUnit #308 (high)",
                    "web_search": True,
                    "request_id": "req-ops-1",
                },
            )
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertIn("Unit #308", body["answer"])
        self.assertEqual(body["task"], "board_ops")
        mock_web.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_tokens"], 550)

    def test_sse_streaming_emits_status_deltas_and_done_then_releases_lock(self):
        with patch.object(server, "_cf_ai_stream", return_value=iter(["Check ", "air dryer", " purge valve."])), patch.object(
            server, "_ai_web_search", return_value={"status": "ok", "results": []}
        ):
            res = self.client.post(
                "/api/ai/chat",
                headers={"Accept": "text/event-stream"},
                json={"message": "Unit has an air leak near compressor", "web_search": False, "stream": True},
            )
            self.assertEqual(res.status_code, 200)
            self.assertIn("text/event-stream", res.content_type)
            raw = res.get_data(as_text=True)

        self.assertIn("event: status", raw)
        self.assertIn("event: delta", raw)
        self.assertIn("event: done", raw)
        done_block = raw.rsplit("event: done\ndata: ", 1)[1].split("\n\n", 1)[0]
        done_payload = json.loads(done_block)
        self.assertEqual(done_payload["answer"], "Check air dryer purge valve.")

        # Subsequent request must succeed immediately (stream lock cleanly released)
        with patch.object(server, "_cf_ai", return_value="Follow-up ok"):
            res2 = self.client.post(
                "/api/ai/chat",
                json={"message": "Got it", "chat_id": done_payload["chat_id"], "web_search": False},
            )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.get_json()["answer"], "Follow-up ok")


if __name__ == "__main__":
    unittest.main()
