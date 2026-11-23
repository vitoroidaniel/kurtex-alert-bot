"""Tests for Manager/Dev Live Board actions, lane rename, proofs edit metadata, user+case batch editing, and bot sync."""
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
from backend.storage import board_order, case_store, db, preference_store, user_store
from bot_app.handlers import report_handler


class AdminActionsAndBotSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        for target, attr in (
            (case_store, "_storage_dir"),
            (preference_store, "_storage_dir"),
            (user_store, "_storage_dir"),
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
        self.agent2 = {"id": 103, "name": "Victor", "role": "agent", "username": "victorr"}
        with db.write_tx(self.data_dir) as conn:
            for person in (self.dev, self.manager, self.agent, self.agent2):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (person["id"], person["name"], person["username"], person["role"]),
                )

    def _auth_session(self, client, user):
        with client.session_transaction() as sess:
            sess["user"] = {"id": user["id"], "name": user["name"], "role": user["role"]}
            sess["workspace_csrf"] = "test-csrf"

    def test_telegram_close_leaves_report_on_board_until_manual_close(self):
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

        # Reported case is visible on Live Board, but hidden from Telegram bot /mycases & active counts.
        bot_active = case_store.get_active_cases_for_agent(self.agent["id"])
        self.assertEqual([c["id"] for c in bot_active], ["ALERT-702"])
        self.assertEqual(case_store.get_active_cases_for_agent(self.agent["id"], status_filter="reported"), [])
        self.assertEqual(case_store.count_active_cases_for_agent(self.agent["id"]), {"assigned": 1, "reported": 0, "total": 1})
        counts, flt, total_pages, page, batch = case_store.get_agent_cases_page_data(self.agent["id"], status_filter="reported")
        self.assertEqual(counts["total"], 1)
        self.assertEqual([c["id"] for c in batch], ["ALERT-702"])
        latest_bot = case_store.get_active_case_for_agent(self.agent["id"])
        self.assertEqual(latest_bot["id"], "ALERT-702")

        client = server.app.test_client()
        self._auth_session(client, self.agent)
        with patch.object(workspace_board, "get_user", return_value=self.agent):
            board = client.get("/api/workspace/board").get_json()
        cards = {c["full_id"]: c for c in board["cases"]}
        self.assertEqual(cards["CASE-701"]["group"], "—")

        closed = case_store.close_case(
            created["id"],
            parts_replaced=True,
            replaced_parts=["ALT-900"],
            closed_by_id=self.agent["id"],
            closed_by_name=self.agent["name"],
        )
        self.assertEqual(closed["status"], "reported")
        self.assertIsNone(closed["closed_at"])
        self.assertTrue(closed["telegram_closed_at"])
        self.assertEqual(closed["replaced_parts"], ["ALT-900"])
        self.assertFalse(any(h.get("action") == "close" for h in closed.get("workspace_history", [])))
        self.assertEqual(case_store.close_case(created["id"]), closed)

        with patch.object(workspace_board, "get_user", return_value=self.agent):
            board_ids = [c["full_id"] for c in client.get("/api/workspace/board").get_json()["cases"]]
            done_ids = [c["full_id"] for c in client.get("/api/cases?filter=workspace_closed").get_json()["cases"]]
        self.assertIn("CASE-701", board_ids)
        self.assertNotIn("CASE-701", done_ids)

        edited = case_store.workspace_write(self.agent, {
            "action": "edit",
            "fields": {"group_name": "GR Sam – Chișinău"},
            "revision": case_store.workspace_revision(closed),
        }, created["id"])
        self.assertEqual(edited["status"], "reported")
        with patch.object(workspace_board, "get_user", return_value=self.agent):
            cards = client.get("/api/workspace/board").get_json()["cases"]
        self.assertEqual(next(c["group"] for c in cards if c["full_id"] == "CASE-701"), "GR Sam – Chișinău")

        manually_closed = case_store.workspace_write(self.agent, {
            "action": "close",
            "revision": case_store.workspace_revision(edited),
        }, created["id"])
        self.assertEqual(manually_closed["status"], "done")
        with patch.object(workspace_board, "get_user", return_value=self.agent):
            board_ids = [c["full_id"] for c in client.get("/api/workspace/board").get_json()["cases"]]
            done_ids = [c["full_id"] for c in client.get("/api/cases?filter=workspace_closed").get_json()["cases"]]
        self.assertNotIn("CASE-701", board_ids)
        self.assertIn("CASE-701", done_ids)

    def test_board_clear_keeps_cases_in_database_and_enforces_roles(self):
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

        self._auth_session(client, self.agent)
        with patch.object(workspace_board, "get_user", return_value=self.agent):
            self.assertEqual(client.post("/api/workspace/board/clear", headers=headers, json={"confirm": True}).status_code, 403)
            self.assertEqual(client.post("/api/workspace/board/close-all", headers=headers, json={"confirm": True}).status_code, 403)
            self.assertEqual(client.post("/api/workspace/order/reset", headers=headers, json={}).status_code, 403)

        self._auth_session(client, self.manager)
        with patch.object(workspace_board, "get_user", return_value=self.manager):
            self.assertEqual(client.post("/api/workspace/board/clear", headers=headers, json={}).status_code, 400)
            close_res = client.post(
                "/api/workspace/board/close-all",
                headers=headers,
                json={"scope": "active", "confirm": True},
            )
            self.assertEqual(close_res.status_code, 200)
            self.assertEqual(close_res.get_json()["closed_count"], 1)
            self.assertEqual(close_res.get_json()["skipped_count"], 1)

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
            self.assertTrue(clear_payload["preserved_in_db"])
            self.assertEqual(client.get("/api/workspace/board").get_json()["cases"], [])
            self.assertEqual(client.get("/api/cases?filter=workspace_closed").get_json()["cases"], [])
            # Verify cases and attachments remain safely in SQLite DB and CSV export
            preserved = case_store.get_case("CASE-801")
            self.assertIsNotNone(preserved)
            self.assertTrue(preserved["board_hidden"])
            self.assertTrue((upload_dir / "sample.txt").is_file())
            export_csv = client.get("/api/export").get_data(as_text=True)
            self.assertIn("CASE-801", export_csv)

    def test_lane_rename_proofs_metadata_and_user_case_batch_edit(self):
        c1 = case_store.workspace_write(
            self.manager,
            {
                "id": "CASE-810",
                "request_id": "req-810",
                "driver": "Sam",
                "unit_number": "810",
                "issue": "Air compressor leak",
                "vehicle_type": "truck",
                "priority": "medium",
                "board_stage": "dealer_shop",
                "target_agent": self.agent,
            },
        )
        case_store.workspace_attachment_write(
            self.agent,
            c1["id"],
            attachment={
                "id": "att-1",
                "name": "shop_invoice.pdf",
                "extension": ".pdf",
                "size": 1200,
                "category": "proof",
                "proof_type": "invoice",
                "version_num": 2,
                "versions": [{"id": "b1", "version": 1}, {"id": "b2", "version": 2}],
                "uploaded_by": "Alex",
                "uploaded_by_id": str(self.agent["id"]),
                "created_at": "2026-11-16T12:00:00Z",
            },
        )

        client = server.app.test_client()
        headers = {"X-Workspace-CSRF": "test-csrf"}
        self._auth_session(client, self.manager)
        with patch.object(workspace_board, "get_user", return_value=self.manager):
            # 1. Rename built-in + custom lane
            lane_add = client.post("/api/workspace/lanes", headers=headers, json={"name": "Night Shift", "color": "#7c3aed"})
            self.assertEqual(lane_add.status_code, 201)
            custom_id = lane_add.get_json()["lane"]["id"]

            r_custom = client.patch(f"/api/workspace/lanes/{custom_id}", headers=headers, json={"name": "Night Ops"})
            self.assertEqual(r_custom.status_code, 200)
            self.assertEqual(r_custom.get_json()["name"], "Night Ops")

            r_builtin = client.patch("/api/workspace/lanes/stage:dealer_shop", headers=headers, json={"name": "External Vendor"})
            self.assertEqual(r_builtin.status_code, 200)
            self.assertEqual(r_builtin.get_json()["lane_labels"]["dealer_shop"], "External Vendor")

            order_data = client.get("/api/workspace/order").get_json()
            self.assertEqual(order_data["lane_labels"]["dealer_shop"], "External Vendor")

            # 2. Proofs endpoint includes edit permissions & versions
            proofs = client.get("/api/workspace/proofs").get_json()["proofs"]
            self.assertEqual(len(proofs), 1)
            self.assertTrue(proofs[0]["can_edit"])
            self.assertEqual(proofs[0]["version_num"], 2)
            self.assertEqual(len(proofs[0]["versions"]), 2)

        # 3. Edit user profile metadata + batch update user's Live Board cases
        patch_user = client.patch(
            f"/api/developer/users/{self.agent['id']}",
            headers=headers,
            json={"name": "Alex R", "username": "alex_new", "role": "agent", "phone": "555-0102", "title": "Lead Tech"},
        )
        self.assertEqual(patch_user.status_code, 200)
        self.assertEqual(patch_user.get_json()["synced_cases"], 1)
        self.assertEqual(case_store.get_case("CASE-810")["agent_name"], "Alex R")

        uc_get = client.get(f"/api/developer/users/{self.agent['id']}/cases").get_json()
        self.assertEqual(len(uc_get["active_cases"]), 1)
        self.assertEqual(uc_get["user"]["phone"], "555-0102")

        uc_post = client.post(
            f"/api/developer/users/{self.agent['id']}/cases",
            headers=headers,
            json={
                "reassign_to_id": str(self.agent2["id"]),
                "board_stage": "monitoring",
                "priority": "high",
                "shop_info": "Love's Bay 3",
            },
        )
        self.assertEqual(uc_post.status_code, 200)
        self.assertEqual(uc_post.get_json()["updated_count"], 1)
        moved = case_store.get_case("CASE-810")
        self.assertEqual(moved["agent_id"], self.agent2["id"])
        self.assertEqual(moved["board_stage"], "monitoring")
        self.assertEqual(moved["priority"], "high")
        self.assertEqual(moved["shop_info"], "Love's Bay 3")

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
