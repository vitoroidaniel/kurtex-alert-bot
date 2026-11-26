import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from scripts.audit_workspace_attachments import audit


class AttachmentInventoryTests(unittest.TestCase):
    def test_missing_orphan_and_version_detection_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "fixture.sqlite3"
            uploads = root / "workspace_uploads"
            folder = uploads / "case"
            folder.mkdir(parents=True)
            (folder / "latest.pdf").write_bytes(b"current")
            (folder / "orphan.txt").write_bytes(b"unreferenced")
            with sqlite3.connect(db) as connection:
                connection.execute("CREATE TABLE cases(id TEXT, data_json TEXT)")
                data = {"workspace_attachments":[{"id":"a", "blob_id":"latest",
                          "extension":".pdf", "versions":[{"blob_id":"previous",
                          "extension":".pdf"}]}]}
                connection.execute("INSERT INTO cases VALUES(?,?)", ("case", json.dumps(data)))
            before = db.read_bytes()
            report = audit(db, uploads)
            self.assertEqual(report["referenced_blobs"], 2)
            self.assertEqual(report["missing_blobs"], 1)
            self.assertEqual(report["unreferenced_candidates"], 1)
            self.assertEqual(db.read_bytes(), before)
            self.assertTrue((folder / "orphan.txt").is_file())
