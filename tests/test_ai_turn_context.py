import unittest
from backend.ai.turn_context import select_attachment_ids, is_social_turn, is_weather_turn

class TurnContextTests(unittest.TestCase):
    def setUp(self):
        self.history = [{"role":"user","attachment_ids":["old"]},{"role":"assistant","content":"Truck photo"}]
        self.available = {"old":{}, "new":{}}
    def test_unrelated_turns_do_not_reuse_media(self):
        for msg in ("fck off","duten pula","weather today","top 3 most expensive repairs","hello"):
            self.assertEqual(select_attachment_ids(msg, [], self.history, self.available), [])
    def test_explicit_attachment(self):
        self.assertEqual(select_attachment_ids("this?", ["new"], self.history, self.available), ["new"])
    def test_media_followup(self):
        self.assertEqual(select_attachment_ids("what is this?", [], self.history, self.available), ["old"])
    def test_no_old_media_after_topic_change(self):
        history=self.history+[{"role":"user","attachment_ids":[]}]
        self.assertEqual(select_attachment_ids("this?", [], history, self.available), [])
    def test_social_and_weather(self):
        self.assertTrue(is_social_turn("fck off"))
        self.assertTrue(is_weather_turn("weather today"))
        self.assertFalse(is_social_turn("repair truck"))
