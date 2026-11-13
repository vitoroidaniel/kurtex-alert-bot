"""Dashboard-only issue reporting API and preserved case workflows."""
from pathlib import Path
import ast
import unittest

ROOT=Path(__file__).resolve().parents[1]


class IssueCenterAPITests(unittest.TestCase):
    def test_dashboard_routes_and_permissions(self):
        source=(ROOT/'api/server.py').read_text(encoding='utf-8')
        ast.parse(source)
        for token in (
            'def api_bot_feedback_list():',
            '@app.route("/api/bot/feedback", methods=["GET", "POST"])',
            'def api_bot_feedback_mine():',
            'def api_bot_feedback_messages(feedback_id):',
            'def api_bot_feedback_media(feedback_id, message_id):',
            'def _feedback_thread_access(feedback_id: str):',
            '_feedback_csrf_error()',
            'feedback_store.get_feedback_by_user(feedback_id',
            'file_key = secrets.token_hex(20)',
            'DATA_DIR / "feedback_uploads"',
            'Resolved reports are read-only.',
            'def _cached_home_briefing(',
            'request.args.get("lite") == "1"',
            'if "report_issue" not in data[role]',
        ):
            self.assertIn(token,source)
        self.assertNotIn('def _notify_feedback_reporter(',source)
        self.assertNotIn('text="Reply from Kurtex support:',source)

    def test_telegram_bug_commands_removed_but_case_and_alert_commands_preserved(self):
        bot=(ROOT/'bot.py').read_text(encoding='utf-8')
        handler=(ROOT/'bot_app/handlers/feedback_handler.py').read_text(encoding='utf-8')
        ast.parse(bot)
        ast.parse(handler)
        for forbidden in ('get_feedback_conversation()', 'get_ticket_conversation()',
                          'CommandHandler("bug"', 'CommandHandler("support"',
                          'CommandHandler("myreports"'):
            self.assertNotIn(forbidden,bot+handler)
        self.assertIn('app.add_handler(get_alert_broadcast_conversation())',bot)
        self.assertIn('app.add_handler(get_report_conversation())',bot)
        self.assertIn('def get_alert_broadcast_conversation()',handler)
        self.assertIn('get_user(user.id)',handler)


if __name__=='__main__':
    unittest.main()
