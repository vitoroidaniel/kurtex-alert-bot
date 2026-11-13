"""v198 conversation endpoints and lightweight briefing source regressions."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FeedbackEndpointsTests(unittest.TestCase):
    def test_thread_api_has_server_side_access_controls(self):
        source = (ROOT/'api/server.py').read_text(encoding='utf-8')
        for token in (
            'def _feedback_thread_access(feedback_id: str):',
            'def api_bot_feedback_mine():',
            'def api_bot_feedback_messages(feedback_id):',
            'def api_bot_feedback_media(feedback_id, message_id):',
            '_feedback_csrf_error()',
            '"Telegram delivery failed. Nothing was saved."',
            'get_feedback_media(feedback_id, message_id)',
            'Resolved tickets cannot receive new messages.',
            'Attachment captions are limited to 1024 characters.',
        ):
            self.assertIn(token, source)
        ast.parse(source)

    def test_briefing_cache_and_fast_history_path(self):
        source = (ROOT/'api/server.py').read_text(encoding='utf-8')
        self.assertIn('@lru_cache(maxsize=64)', source)
        self.assertIn('def _cached_home_briefing(', source)
        self.assertIn('int(time.time() // 30)', source)
        self.assertIn('request.args.get("lite") == "1"', source)

    def test_bot_commands_registered(self):
        source = (ROOT/'bot.py').read_text(encoding='utf-8')
        handler = (ROOT/'bot_app/handlers/feedback_handler.py').read_text(encoding='utf-8')
        self.assertIn('get_ticket_conversation()', source)
        self.assertIn('CommandHandler("myreports"', handler)
        self.assertIn('async def recv_ticket_update(', handler)
        self.assertIn('filters.PHOTO', handler)
        ast.parse(handler)
        ast.parse(source)


if __name__ == '__main__':
    unittest.main()
