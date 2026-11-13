"""Regression checks for dashboard support schema on a fresh volume."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.storage import db, feedback_store


class DashboardReleaseStorageTests(unittest.TestCase):
    def test_new_report_diagnostics_and_release_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {'DATA_DIR': temp}):
                with patch.object(feedback_store, 'DATA_DIR', Path(temp)):
                    item = feedback_store.create_feedback(
                        user_id=42, user_name='Tester', username='tester',
                        user_role='agent', category='performance',
                        message='Dashboard loading slowly', diagnostics={'page': 'overview'},
                    )
                    loaded = feedback_store.get_feedback(item['id'])
                    self.assertEqual(loaded['diagnostics']['page'], 'overview')
                    self.assertEqual(loaded['category'], 'performance')
                    with db.read_conn(temp) as conn:
                        conn.execute("SELECT * FROM dashboard_release_notes LIMIT 1")


if __name__ == '__main__':
    unittest.main()
