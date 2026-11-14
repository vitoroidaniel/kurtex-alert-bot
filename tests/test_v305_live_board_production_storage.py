"""Production Live Board storage tests: normalized tables, fractional rank, field-level atomic merge, and delta stream."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.storage import case_store, db


class LiveBoardProductionStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.patch_dir = patch.object(case_store, "_storage_dir", return_value=self.data_dir)
        self.patch_dir.start()
        self.addCleanup(self.patch_dir.stop)
        self.daniel = {"id": 101, "name": "Daniel V.", "role": "super_admin", "username": "danielv"}
        self.max_agent = {"id": 102, "name": "Max R.", "role": "agent", "username": "maxr"}
        with db.write_tx(self.data_dir) as conn:
            for person in (self.daniel, self.max_agent):
                conn.execute(
                    "INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)",
                    (person["id"], person["name"], person["username"], person["role"]),
                )
        self.case = case_store.workspace_write(
            self.daniel,
            {
                "id": "CASE-8501",
                "request_id": "req-8501",
                "driver": "Marques M.",
                "unit_number": "R391",
                "issue": "Fuel pump pressure loss",
                "vehicle_type": "truck",
                "priority": "high",
                "target_agent": {"id": 102, "name": "Max R.", "username": "maxr"},
            },
        )

    def test_normalized_columns_and_event_stream_sync(self):
        cursor0 = db.get_latest_event_cursor(self.data_dir)
        self.assertGreater(cursor0, 0)
        rev = case_store.workspace_revision(self.case)
        updated = case_store.workspace_write(
            self.max_agent,
            {
                "action": "edit",
                "revision": rev,
                "fields": {"shop_info": "TA Petro Joplin #42", "amount": "450.00", "pickup": "Chicago, IL", "delivery": "Dallas, TX"},
            },
            "CASE-8501",
        )
        note_rev = case_store.workspace_revision(updated)
        case_store.workspace_write(
            self.max_agent,
            {"action": "note", "revision": note_rev, "text": "Parts ordered @danielv", "file_ids": []},
            "CASE-8501",
        )
        with db.read_conn(self.data_dir) as conn:
            row = conn.execute(
                "SELECT priority, vehicle_type, shop_info, amount, pickup, delivery, notes_count FROM cases WHERE id='CASE-8501'"
            ).fetchone()
            note_rows = conn.execute("SELECT author_name, body FROM case_notes WHERE case_id='CASE-8501'").fetchall()
        self.assertEqual(row["priority"], "high")
        self.assertEqual(row["vehicle_type"], "truck")
        self.assertEqual(row["shop_info"], "TA Petro Joplin #42")
        self.assertEqual(row["amount"], "450.00")
        self.assertEqual(row["pickup"], "Chicago, IL")
        self.assertEqual(row["delivery"], "Dallas, TX")
        self.assertEqual(row["notes_count"], 1)
        self.assertEqual(len(note_rows), 1)
        self.assertEqual(note_rows[0]["body"], "Parts ordered @danielv")

        events = db.get_events_since(cursor0, data_dir=self.data_dir)
        self.assertEqual([e["event_type"] for e in events], ["edit", "note"])

    def test_fractional_rank_single_row_update(self):
        ranked = case_store.workspace_write(
            self.max_agent,
            {"action": "rank", "board_rank": 1500.5},
            "CASE-8501",
        )
        self.assertEqual(ranked["board_rank"], 1500.5)
        with db.read_conn(self.data_dir) as conn:
            row = conn.execute("SELECT board_rank FROM cases WHERE id='CASE-8501'").fetchone()
        self.assertEqual(float(row["board_rank"]), 1500.5)

    def test_field_level_atomic_merge_prevents_false_conflicts(self):
        base_case = case_store.get_case("CASE-8501")
        shared_rev = case_store.workspace_revision(base_case)

        # Agent Max updates shop_info + amount first
        case_store.workspace_write(
            self.max_agent,
            {
                "action": "edit",
                "revision": shared_rev,
                "fields": {"shop_info": "Love's #210", "amount": "310.00"},
                "base_fields": {"shop_info": "", "amount": ""},
            },
            "CASE-8501",
        )

        # Manager Daniel concurrently saves pickup + delivery using the older revision,
        # which succeeds cleanly because pickup & delivery were untouched!
        merged = case_store.workspace_write(
            self.daniel,
            {
                "action": "edit",
                "revision": shared_rev,
                "fields": {"pickup": "Detriot, MI", "delivery": "Atlanta, GA"},
                "base_fields": {"pickup": "", "delivery": ""},
            },
            "CASE-8501",
        )
        self.assertEqual(merged["shop_info"], "Love's #210")
        self.assertEqual(merged["amount"], "310.00")
        self.assertEqual(merged["pickup"], "Detriot, MI")
        self.assertEqual(merged["delivery"], "Atlanta, GA")

        # However, if someone tries to overwrite shop_info using the stale base value "", it is rejected!
        with self.assertRaises(ValueError):
            case_store.workspace_write(
                self.daniel,
                {
                    "action": "edit",
                    "revision": shared_rev,
                    "fields": {"shop_info": "Stale Shop Override"},
                    "base_fields": {"shop_info": ""},
                },
                "CASE-8501",
            )

    def test_presence_draft_preview_and_modular_workspace_routes(self):
        import uuid
        from backend.storage.board_presence import heartbeat, snapshot
        from api import server

        heartbeat(
            str(uuid.uuid4()),
            22,
            "Max R.",
            "editing",
            "CASE-8501",
            "issue",
            self.data_dir,
            draft="Fuel pump pressure loss",
        )
        heartbeat(
            str(uuid.uuid4()),
            33,
            "Andrei P.",
            "editing",
            "CASE-8472",
            "note",
            self.data_dir,
            draft="Adding Thermo King dispatch...",
        )
        snap = snapshot(11, self.data_dir)
        self.assertEqual(snap["online"], 2)
        self.assertEqual(snap["editing"], 2)
        self.assertEqual(len(snap["peers"]), 2)
        drafts = {p["name"]: p["draft"] for p in snap["peers"]}
        self.assertEqual(drafts["Max R."], "Fuel pump pressure loss")
        self.assertEqual(drafts["Andrei P."], "Adding Thermo King dispatch...")
        self.assertTrue(callable(getattr(server, "api_workspace_board", None)))
        self.assertTrue(callable(getattr(server, "api_workspace_write", None)))


if __name__ == "__main__":
    unittest.main()
