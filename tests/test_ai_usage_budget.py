import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from backend.ai.usage_budget import admit
from backend.ai.turn_context import is_live_time_turn

class UsageBudgetTests(unittest.TestCase):
    def test_concurrent_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"budget.sqlite3"
            self.assertTrue(admit(path,"agent","chat",5,20,60))
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(lambda _:admit(path,"agent","chat",5,20,60),range(12)))
            self.assertEqual(sum(results),4)
            self.assertTrue(admit(path,"other","chat",5,20,60))
    def test_live_clock(self):
        self.assertTrue(is_live_time_turn("time in Dallas"))
        self.assertFalse(is_live_time_turn("repair cost"))
