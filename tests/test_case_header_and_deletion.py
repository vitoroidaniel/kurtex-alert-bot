"""Isolated SQLite/API regressions for case identity and the two delete modes."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from api import server
from api.routes import workspace_board
from backend.ai.fleet_knowledge import FleetKnowledgeStore
from backend.storage import board_order, case_store, db, preference_store, user_store


class CaseHeaderAndDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        for target, attr in ((case_store, "_storage_dir"), (preference_store, "_storage_dir"),
                             (user_store, "_storage_dir")):
            self.enterContext(patch.object(target, attr, return_value=self.data_dir))
        self.enterContext(patch.object(server, "DATA_DIR", self.data_dir))
        self.knowledge = FleetKnowledgeStore(self.data_dir / "fleet_knowledge.sqlite3")
        self.enterContext(patch.object(server, "fleet_knowledge_store", self.knowledge))
        self.manager = {"id": 101, "name": "Manager", "role": "super_admin"}
        self.dev = {"id": 100, "name": "Dev", "role": "developer"}
        self.agent = {"id": 102, "name": "Agent", "role": "agent"}
        with db.write_tx(self.data_dir) as conn:
            for user in (self.manager, self.dev, self.agent):
                conn.execute("INSERT INTO users(id,name,role) VALUES(?,?,?)",
                             (user["id"], user["name"], user["role"]))
        self.client = server.app.test_client()
        self.auth(self.manager)
        self.case = self.create("CASE-NEW")
        self.other = self.create("CASE-KEEP")
        self.headers = {"X-Workspace-CSRF": "test-csrf"}

    def auth(self, user):
        with self.client.session_transaction() as sess:
            sess["user"] = user
            sess["workspace_csrf"] = "test-csrf"

    def create(self, cid):
        return case_store.workspace_write(self.manager, {
            "id": cid, "request_id": "req-" + cid, "driver": "Sam", "unit_number": "902",
            "issue": "Alternator failure on the road", "vehicle_type": "truck",
            "priority": "high", "target_agent": self.agent,
        })

    def delete(self, mode="board", **overrides):
        data = {"mode": mode, "confirm": True, "revision": case_store.workspace_revision(self.case)}
        data.update(overrides)
        return self.client.delete("/api/workspace/cases/CASE-NEW", headers=self.headers, json=data)

    def seed_related(self):
        cid = self.case["id"]
        case_store.workspace_attachment_write(self.manager, cid, attachment={
            "id": "a" * 32, "name": "invoice.txt", "extension": ".txt", "size": 5,
            "category": "proof", "uploaded_by": "Manager", "uploaded_by_id": "101",
        })
        current = case_store.get_case(cid)
        case_store.workspace_write(self.manager, {"action": "note", "text": "Private case conversation",
            "file_ids": [], "revision": case_store.workspace_revision(current)}, cid)
        with db.write_tx(self.data_dir) as conn:
            current = case_store.get_case(cid)
            current["media"] = [["photo", "telegram-file"], {"type": "document", "file_id": "telegram-doc"}]
            case_store._upsert_case_row(conn, current)
            conn.execute("INSERT INTO active_alerts(alert_id,data_json) VALUES(?,?)", (cid, '{}'))
            conn.execute("INSERT INTO notifications(id,user_key,data_json) VALUES(?,?,?)",
                         ("case-notice", "101", json.dumps({"case_id": cid, "title": "Private"})))
        self.case = case_store.get_case(cid)
        self.upload = self.data_dir / "workspace_uploads" / cid
        (self.upload / "versions").mkdir(parents=True)
        (self.upload / "invoice.txt").write_text("proof")
        (self.upload / "versions" / "old.txt").write_text("old proof")
        cache = self.data_dir / "telegram_media_cache"
        cache.mkdir()
        for fid in ("telegram-file", "telegram-doc"):
            for ext in (".bin", ".json"):
                (cache / (hashlib.sha256(fid.encode()).hexdigest() + ext)).write_text("cached")
        preference_store.save_case_reads(101, {cid: "2026-11-17T12:00:00Z"})
        preference_store.save_workspace_board_prefs(101, {"watchlist_ids": [cid, "CASE-KEEP"]})
        board_order.save_order("agent_workflow:agent:102", [cid, "CASE-KEEP"], 0, "101", manager=True, data_dir=self.data_dir)
        self.knowledge.ingest_cases([self.case, self.other])

    def test_header_edit_persists_in_detail_and_normalized_storage(self):
        response = self.client.patch("/api/workspace/cases/CASE-NEW", headers=self.headers, json={
            "action": "edit", "revision": case_store.workspace_revision(self.case),
            "fields": {"unit_number": "R391", "vehicle_type": "reefer", "report_driver": "Alex Doe"},
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        detail = self.client.get("/api/case?id=CASE-NEW").get_json()
        self.assertEqual((detail["unit_number"], detail["vehicle_type"], detail["report_driver"]),
                         ("R391", "reefer", "Alex Doe"))
        stored = case_store.get_case("CASE-NEW")
        self.assertEqual(stored["report_data"]["driver"], "Alex Doe")
        self.assertIn("edit", [h["action"] for h in stored["workspace_history"]])

    def test_board_removal_preserves_record_notes_and_files(self):
        self.seed_related()
        result = self.delete()
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertTrue(result.get_json()["preserved_in_db"])
        stored = case_store.get_case("CASE-NEW")
        self.assertTrue(stored["board_hidden"])
        self.assertEqual(stored["status"], "done")
        self.assertEqual(len(stored["workspace_notes"]), 1)
        self.assertTrue((self.upload / "invoice.txt").exists())
        self.assertEqual(len(stored["workspace_attachments"]), 1)
        self.assertNotIn("CASE-NEW", json.dumps(self.client.get("/api/workspace/board").get_json()))
        self.assertNotIn("CASE-NEW", json.dumps(self.client.get("/api/cases?filter=workspace_closed").get_json()))
        self.assertIn("CASE-NEW", self.client.get("/api/export").get_data(as_text=True))
        self.assertIsNotNone(case_store.get_case("CASE-KEEP"))

    def test_permanent_removal_purges_content_files_index_and_related_rows(self):
        self.seed_related()
        cursor = db.get_latest_event_cursor(self.data_dir)
        result = self.delete("permanent")
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertFalse(result.get_json()["preserved_in_db"])
        self.assertFalse(result.get_json()["file_cleanup_pending"])
        self.assertIsNone(case_store.get_case("CASE-NEW"))
        self.assertIsNotNone(case_store.get_case("CASE-KEEP"))
        self.assertFalse(self.upload.exists())
        self.assertEqual(list((self.data_dir / "telegram_media_cache").iterdir()), [])
        with db.read_conn(self.data_dir) as conn:
            for table in ("case_notes", "case_attachments", "workspace_presence"):
                self.assertEqual(conn.execute(f"SELECT count(*) FROM {table} WHERE case_id='CASE-NEW'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM active_alerts WHERE alert_id='CASE-NEW'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM notifications").fetchone()[0], 0)
            events = conn.execute("SELECT * FROM case_events WHERE case_id='CASE-NEW'").fetchall()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "deleted")
            self.assertEqual(events[0]["summary_json"], "{}")
        self.assertNotIn("CASE-NEW", preference_store.get_case_reads(101))
        self.assertEqual(preference_store.get_workspace_board_prefs(101)["watchlist_ids"], ["CASE-KEEP"])
        self.assertEqual(board_order.read_order(self.data_dir)["groups"]["agent_workflow:agent:102"], ["CASE-KEEP"])
        self.knowledge.ingest_cases([self.case])  # An old in-flight snapshot must not restore evidence.
        self.assertEqual(self.knowledge.stats()["total"], 1)
        self.assertEqual(self.client.get("/api/case?id=CASE-NEW").status_code, 404)
        self.assertNotIn("CASE-NEW", self.client.get("/api/export").get_data(as_text=True))
        delta = self.client.get(f"/api/workspace/board?cursor={cursor}").get_json()
        self.assertIn("CASE-NEW", delta["removed_ids"])

    def test_permission_csrf_confirmation_and_mode_are_required(self):
        self.assertEqual(self.delete(confirm=False).status_code, 400)
        self.assertEqual(self.delete("unknown").status_code, 400)
        self.assertEqual(self.client.delete("/api/workspace/cases/CASE-NEW", json={"confirm": True}).status_code, 403)
        self.auth(self.agent)
        # Agents cannot permanently delete cases or skip confirmation, even with CSRF.
        self.assertEqual(self.delete("permanent").status_code, 403)
        self.assertEqual(self.delete("board", confirm=False).status_code, 400)
        self.assertIsNotNone(case_store.get_case("CASE-NEW"))

    def test_agent_can_remove_any_non_test_case_from_board_with_confirmation(self):
        other_manager_case = case_store.workspace_write(self.manager, {
            "id": "CASE-MGR-1", "request_id": "req-mgr-1", "driver": "Lee", "unit_number": "411",
            "issue": "Wheel seal leak", "vehicle_type": "truck",
            "priority": "medium", "target_agent": self.manager,
        })
        self.auth(self.agent)
        detail = self.client.get("/api/case?id=CASE-MGR-1").get_json()
        self.assertTrue(detail["can_delete_board"])
        self.assertFalse(detail["can_delete_permanent"])
        board_rows = self.client.get("/api/workspace/board").get_json()["cases"]
        mgr_summary = next(r for r in board_rows if r.get("full_id") == "CASE-MGR-1" or r.get("id") == "CASE-MGR-1")
        self.assertTrue(mgr_summary["can_delete_board"])
        self.assertFalse(mgr_summary["can_delete_permanent"])

        res = self.client.delete("/api/workspace/cases/CASE-MGR-1", headers=self.headers, json={
            "mode": "board",
            "confirm": True,
            "revision": case_store.workspace_revision(other_manager_case),
        })
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertTrue(res.get_json()["preserved_in_db"])
        stored = case_store.get_case("CASE-MGR-1")
        self.assertTrue(stored["board_hidden"])
        self.assertEqual(stored["status"], "done")
        self.assertEqual(stored["workspace_history"][-1]["author_role"], "agent")

    def test_stale_revision_preserves_all_data(self):
        self.seed_related()
        result = self.delete("permanent", revision="stale")
        self.assertEqual(result.status_code, 409)
        self.assertIsNotNone(case_store.get_case("CASE-NEW"))
        self.assertTrue(self.upload.exists())
        self.assertEqual(self.knowledge.stats()["total"], 2)

    def test_test_case_requires_developer(self):
        self.case = case_store.workspace_write(self.dev, {"action": "set_test", "is_test": True}, "CASE-NEW")
        self.assertEqual(self.delete("permanent").status_code, 403)
        self.auth(self.agent)
        self.assertEqual(self.delete("board").status_code, 403)
        self.assertEqual(self.delete("permanent").status_code, 403)
        self.auth(self.dev)
        self.assertEqual(self.delete("permanent").status_code, 200)

    def test_case_history_is_only_returned_to_managers_and_developers(self):
        self.seed_related()
        for user, allowed in ((self.manager, True), (self.dev, True), (self.agent, False)):
            self.auth(user)
            detail = self.client.get("/api/case?id=CASE-NEW").get_json()
            self.assertEqual(detail["can_view_history"], allowed)
            self.assertEqual(bool(detail["workspace_history"]), allowed)
        # Stored role wins over an old manager role left in the login cookie.
        self.auth(self.manager)
        user_store.add_user(101, "Manager", "", "agent")
        detail = self.client.get("/api/case?id=CASE-NEW").get_json()
        self.assertFalse(detail["can_view_history"])
        self.assertEqual(detail["workspace_history"], [])

    def test_already_hidden_case_can_be_permanently_deleted(self):
        self.assertEqual(self.delete().status_code, 200)
        self.case = case_store.get_case("CASE-NEW")
        self.assertEqual(self.delete("permanent").status_code, 200)
        self.assertEqual(self.delete("permanent").status_code, 404)

    def test_file_cleanup_failure_is_reported_honestly(self):
        self.seed_related()
        with patch.object(workspace_board.shutil, "rmtree", side_effect=OSError("File locked")), self.assertLogs(server.logger, level="ERROR"):
            result = self.delete("permanent")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.get_json()["file_cleanup_pending"])
        self.assertIsNone(case_store.get_case("CASE-NEW"))

    def test_agent_can_move_card_away_and_take_it_back(self):
        agent_two = {"id": 103, "name": "AgentTwo", "role": "agent"}
        with db.write_tx(self.data_dir) as conn:
            conn.execute("INSERT INTO users(id,name,role) VALUES(?,?,?)", (agent_two["id"], agent_two["name"], agent_two["role"]))

        # Agent owns CASE-NEW -> moves it to AgentTwo -> loses can_manage, keeps can_reassign -> takes it back!
        self.auth(self.agent)
        move_away = self.client.patch(
            "/api/workspace/cases/CASE-NEW",
            headers=self.headers,
            json={
                "action": "move",
                "agent_id": agent_two["id"],
                "stage": "",
                "allow_concurrent_move": True,
                "revision": case_store.workspace_revision(self.case),
            },
        )
        self.assertEqual(move_away.status_code, 200, move_away.get_json())

        board_rows = self.client.get("/api/workspace/board").get_json()["cases"]
        summary = next(r for r in board_rows if r.get("full_id") == "CASE-NEW")
        self.assertEqual(str(summary["agent_id"]), str(agent_two["id"]))
        self.assertFalse(summary["can_manage"])
        self.assertTrue(summary["can_reassign"])

        detail = self.client.get("/api/case?id=CASE-NEW").get_json()
        self.assertFalse(detail["can_manage"])
        self.assertTrue(detail["can_reassign"])

        # Agent moves the card back into their own lane from AgentTwo!
        move_back = self.client.patch(
            "/api/workspace/cases/CASE-NEW",
            headers=self.headers,
            json={
                "action": "move",
                "agent_id": self.agent["id"],
                "stage": "",
                "allow_concurrent_move": True,
                "revision": detail["revision"],
            },
        )
        self.assertEqual(move_back.status_code, 200, move_back.get_json())
        self.assertEqual(str(move_back.get_json()["case"]["agent_id"]), str(self.agent["id"]))

        # Stage move on another agent's card works too; test cases remain blocked for non-developers.
        stage_move = self.client.patch(
            "/api/workspace/cases/CASE-NEW",
            headers=self.headers,
            json={
                "action": "stage",
                "stage": "monitoring",
                "allow_concurrent_move": True,
                "revision": move_back.get_json()["revision"],
            },
        )
        self.assertEqual(stage_move.status_code, 200, stage_move.get_json())

        case_store.workspace_write(self.dev, {"action": "set_test", "is_test": True}, "CASE-NEW")
        test_detail = self.client.get("/api/case?id=CASE-NEW").get_json()
        self.assertFalse(test_detail["can_reassign"])
        blocked_test_move = self.client.patch(
            "/api/workspace/cases/CASE-NEW",
            headers=self.headers,
            json={"action": "move", "agent_id": self.agent["id"], "stage": "", "revision": test_detail["revision"]},
        )
        self.assertEqual(blocked_test_move.status_code, 403)


if __name__ == "__main__":
    unittest.main()
