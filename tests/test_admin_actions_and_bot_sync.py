"""Tests for Manager/Dev Live Board actions, bot close -> Done sync, and prefill removal."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from api import server
from api.routes import workspace_board
from backend.storage import board_order, case_store, db, preference_store
from bot_app.handlers import report_handler


class AdminActionsAndBotSyncTests(unittest.TestCase):
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

        self.dev = {"id": 100, "name": "Dev", "role": "developer", "username": "dev"}
        self.manager = {"id": 101, "name": "Daniel", "role": "super_admin", "username": "danielv"}
        self.agent = {"id": 102, "name": "Alex", "role": "agent", "username": "alexr"}
        with db.write_tx(self.data_dir) as conn:
            for person in (self.dev, self.manager, self.agent):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (person["id"], person["name"], person["username"], person["role"]),
                )

    def _auth_session(self, client, user):
        with client.session_transaction() as sess:
            sess["user"] = {"id": user["id"], "name": user["name"], "role": user["role"]}
            sess["workspace_csrf"] = "test-csrf"

    def test_telegram_close_reported_case_moves_to_live_board_done(self):
        created = case_store.workspace_write(
            self.manager,
            {
                "id": "CASE-701",
                "request_id": "req-701",
                "driver": "Sam",
                "unit_number": "701",
                "issue": "Alternator failure",
                "vehicle_type": "truck",
                "priority": "high",
                "target_agent": self.agent,
            },
        )
        # Seed a non-reported alert-only case closed directly in Telegram.
        with db.write_tx(self.data_dir) as conn:
            alert_only = {
                "id": "ALERT-702",
                "status": "assigned",
                "agent_id": self.agent["id"],
                "agent_name": self.agent["name"],
                "driver_name": "Bob",
                "group_name": "GR Bob",
                "description": "Quick check",
                "opened_at": "2026-11-16T10:00:00Z",
                "assigned_at": "2026-11-16T10:01:00Z",
            }
            case_store._upsert_case_row(conn, alert_only, event_type="created")

        closed = case_store.close_case(
            created["id"],
            parts_replaced=True,
            replaced_parts=["ALT-900"],
            closed_by_id=self.agent["id"],
            closed_by_name=self.agent["name"],
        )
        self.assertEqual(closed["status"], "done")
        self.assertEqual(closed["closed_by_name"], "Alex")
        self.assertTrue(any(h.get("action") == "close" for h in closed.get("workspace_history", [])))

        closed_alert = case_store.close_case(
            "ALERT-702",
            closed_by_id=self.agent["id"],
            closed_by_name=self.agent["name"],
        )
        self.assertEqual(closed_alert["status"], "done")

        client = server.app.test_client()
        self._auth_session(client, self.agent)
        with patch.object(workspace_board, "get_user", return_value=self.agent):
            board_ids = [c["full_id"] for c in client.get("/api/workspace/board").get_json()["cases"]]
            done_ids = [c["full_id"] for c in client.get("/api/cases?filter=workspace_closed").get_json()["cases"]]

        self.assertNotIn("CASE-701", board_ids)
        self.assertIn("CASE-701", done_ids)
        self.assertNotIn("ALERT-702", done_ids)

    def test_board_admin_endpoints_enforce_role_and_clear_or_close(self):
        c_active = case_store.workspace_write(
            self.manager,
            {
                "id": "CASE-801",
                "request_id": "req-801",
                "driver": "Tim",
                "unit_number": "801",
                "issue": "Brake chamber leak",
                "vehicle_type": "truck",
                "priority": "high",
                "target_agent": self.agent,
            },
        )
        c_test = case_store.workspace_write(
            self.dev,
            {
                "id": "CASE-802",
                "request_id": "req-802",
                "driver": "Test",
                "unit_number": "999",
                "issue": "test issue",
                "vehicle_type": "truck",
                "priority": "low",
                "board_stage": "test",
                "target_agent": self.agent,
            },
        )
        case_store.workspace_write(
            self.dev,
            {"action": "set_test", "is_test": True},
            c_test["id"],
        )
        upload_dir = self.data_dir / "workspace_uploads" / c_active["id"]
        upload_dir.mkdir(parents=True, exist_ok=True)
        (upload_dir / "sample.txt").write_text("hi", encoding="utf-8")

        client = server.app.test_client()
        headers = {"X-Workspace-CSRF": "test-csrf"}

        # Agent blocked on all 3 admin actions
        self._auth_session(client, self.agent)
        with patch.object(workspace_board, "get_user", return_value=self.agent):
            self.assertEqual(client.post("/api/workspace/board/clear", headers=headers, json={"confirm": True}).status_code, 403)
            self.assertEqual(client.post("/api/workspace/board/close-all", headers=headers, json={"confirm": True}).status_code, 403)
            self.assertEqual(client.post("/api/workspace/order/reset", headers=headers, json={}).status_code, 403)

        # Manager allowed, requires confirm=true for destructive actions
        self._auth_session(client, self.manager)
        with patch.object(workspace_board, "get_user", return_value=self.manager):
            self.assertEqual(client.post("/api/workspace/board/clear", headers=headers, json={}).status_code, 400)
            close_res = client.post(
                "/api/workspace/board/close-all",
                headers=headers,
                json={"scope": "active", "confirm": True},
            )
            self.assertEqual(close_res.status_code, 200)
            close_payload = close_res.get_json()
            self.assertEqual(close_payload["closed_count"], 1)
            self.assertEqual(close_payload["skipped_count"], 1)

            reset_res = client.post("/api/workspace/order/reset", headers=headers, json={})
            self.assertEqual(reset_res.status_code, 200)
            self.assertEqual(board_order.read_order(self.data_dir)["groups"], {})

            clear_res = client.post(
                "/api/workspace/board/clear",
                headers=headers,
                json={"scope": "active_and_done", "confirm": True},
            )
            self.assertEqual(clear_res.status_code, 200)
            clear_payload = clear_res.get_json()
            self.assertEqual(clear_payload["deleted_count"], 2)
            self.assertFalse(upload_dir.exists())
            self.assertEqual(client.get("/api/workspace/board").get_json()["cases"], [])
            self.assertEqual(client.get("/api/cases?filter=workspace_closed").get_json()["cases"], [])

    def test_report_handler_has_no_alert_prefill_prompts(self):
        conv = report_handler.get_report_conversation()
        for state_handlers in conv.states.values():
            for h in state_handlers:
                pat = getattr(getattr(h, "pattern", None), "pattern", "") or ""
                self.assertNotIn("rpt_prefill", pat)

        ctx = SimpleNamespace(user_data={"report": {"vehicle_type": "truck"}}, bot_data={})
        update = SimpleNamespace(message=SimpleNamespace(text="123", reply_text=AsyncMock()))
        with patch.object(report_handler, "safe_reply_text", new=AsyncMock()) as mocked_reply:
            state_next = asyncio.run(report_handler.recv_unit(update, ctx))
            self.assertEqual(state_next, report_handler.ASK_DRIVER)
            mocked_reply.assert_awaited_once_with(update.message, "Driver:", parse_mode=None)

            mocked_reply.reset_mock()
            update.message.text = "John Doe"
            state_next = asyncio.run(report_handler.recv_driver(update, ctx))
            self.assertEqual(state_next, report_handler.ASK_ISSUE)
            mocked_reply.assert_awaited_once_with(update.message, "Issue description:", parse_mode=None)


if __name__ == "__main__":
    unittest.main()
