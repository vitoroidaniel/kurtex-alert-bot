"""Verify the real Smart Lists index on isolated synthetic rows."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.storage import smart_lists_store as store


class SmartListIndexTests(unittest.TestCase):
    def test_stable_order_uses_composite_index(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lists.sqlite3"
            with patch.dict(os.environ, {"KURTEX_LISTS_DB": str(path)}):
                ident = store.create_list("Fixture", [{"label": "Unit", "type": "text"}], "fixture")
                with store._db() as conn:
                    conn.executemany(
                        "INSERT INTO entries(id,list_id,values_json,updated_by,updated_at) VALUES(?,?,?,?,?)",
                        ((f"{i:06}", ident, json.dumps({"f0": str(i)}), "fixture", "2026-01-01 00:00:00")
                         for i in range(50000)),
                    )
                    plan = " ".join(str(row["detail"]) for row in conn.execute(
                        "EXPLAIN QUERY PLAN SELECT * FROM entries WHERE list_id=? ORDER BY updated_at DESC,id DESC",
                        (ident,),
                    ))
                    self.assertIn("idx_entries_list_updated_id", plan)
                    self.assertNotIn("TEMP B-TREE", plan.upper())
                    rows = conn.execute(
                        "SELECT id FROM entries WHERE list_id=? ORDER BY updated_at DESC,id DESC LIMIT 20", (ident,)
                    ).fetchall()
                    self.assertEqual([r["id"] for r in rows], [f"{i:06}" for i in range(49999, 49979, -1)])
