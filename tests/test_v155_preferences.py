"""Offline account preference persistence tests (no Telegram or Flask needed)."""
import importlib
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PreferencePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get('DATA_DIR')
        os.environ['DATA_DIR'] = self.tmp.name
        sys.modules.pop('backend.storage.preference_store', None)
        self.store = importlib.import_module('backend.storage.preference_store')

    def tearDown(self):
        if self.old_data_dir is None:
            os.environ.pop('DATA_DIR', None)
        else:
            os.environ['DATA_DIR'] = self.old_data_dir
        self.tmp.cleanup()

    def test_interface_survives_reload_without_erasing_other_preferences(self):
        self.store.save_overview(42, {'desktop': {'order': ['metrics']}})
        self.store.save_home_notes(42, [{'text': 'test'}])
        self.store.save_interface(42, {'theme': 'dark', 'notifyAssigned': False})
        self.assertEqual(self.store.get_interface(42)['notifyAssigned'], False)
        self.store.save_interface(42, {'notifyMissed': True})
        self.assertEqual(self.store.get_interface(42), {'theme': 'dark', 'notifyAssigned': False, 'notifyMissed': True})
        self.assertEqual(self.store.get_overview(42)['desktop']['order'], ['metrics'])
        self.assertEqual(self.store.get_home_notes(42)[0]['text'], 'test')
        self.assertEqual(self.store.get_interface(43), {})
        self.assertEqual(json.loads(self.store.PREFERENCES_FILE.read_text())['42']['interface']['theme'], 'dark')

    def test_concurrent_settings_and_notes_do_not_overwrite(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            work = []
            for i in range(60):
                work.append(pool.submit(self.store.save_interface, 42, {'theme': 'dark'}))
                work.append(pool.submit(self.store.save_home_notes, 42, [{'text': str(i)}]))
            for future in work:
                future.result()
        self.assertEqual(self.store.get_interface(42)['theme'], 'dark')
        self.assertEqual(len(self.store.get_home_notes(42)), 1)


if __name__ == '__main__':
    unittest.main()
