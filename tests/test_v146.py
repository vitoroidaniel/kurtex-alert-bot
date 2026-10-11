"""Self-contained bot regression tests. Run: python tests/test_v146.py

Uses lightweight Telegram UI stubs to exercise the handler logic without a bot token
or any network calls. Also verifies actual JSON case persistence and AI indexing.
"""
import asyncio
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class InlineKeyboardButton:
    def __init__(self, text, callback_data=None, **kwargs):
        self.text = text
        self.callback_data = callback_data
        self.kwargs = kwargs

class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard

telegram = types.ModuleType('telegram')
telegram.Update = object
telegram.InlineKeyboardButton = InlineKeyboardButton
telegram.InlineKeyboardMarkup = InlineKeyboardMarkup
telegram_ext = types.ModuleType('telegram.ext')
telegram_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
for name in ('ConversationHandler', 'CallbackQueryHandler', 'CommandHandler', 'MessageHandler'):
    setattr(telegram_ext, name, type(name, (), {'END': -1}))
telegram_ext.filters = types.SimpleNamespace()
telegram_error = types.ModuleType('telegram.error')
telegram_error.TelegramError = Exception
telegram_constants = types.ModuleType('telegram.constants')
telegram_constants.ParseMode = types.SimpleNamespace(MARKDOWN='Markdown')
sys.modules.update({'telegram': telegram, 'telegram.ext': telegram_ext,
                    'telegram.error': telegram_error, 'telegram.constants': telegram_constants})

class Query:
    def __init__(self, data):
        self.data = data
        self.edits = []
        self.answered = False
    async def answer(self, *args, **kwargs):
        self.answered = True
    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

class Message:
    def __init__(self, text):
        self.text = text
        self.replies = []
    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))

class Update:
    def __init__(self, query=None, message=None):
        self.callback_query = query
        self.message = message
        self.effective_user = types.SimpleNamespace(id=42, first_name='Agent', last_name='Test')

class Context:
    def __init__(self):
        self.user_data = {}
        self.bot_data = {}

async def run_checks(store, agent, report, knowledge):
    async def get_case(cid):
        return store.get_case(cid)
    async def close_case(cid, notes=None, *, parts_replaced=None, replaced_parts=None):
        return store.close_case(cid, notes, parts_replaced=parts_replaced, replaced_parts=replaced_parts)
    async def show_remaining(*args):
        return None
    agent.get_case = get_case
    agent.close_case = close_case
    agent._show_remaining_after = show_remaining

    def new_case(cid):
        store.create_case(cid, 'Driver X', None, 'Group Y', 'Air line leaking')
        store.assign_case(cid, 42, 'Agent Test', None)

    # Existing Report button must show Telegram chat selector, even with public URL configured.
    new_case('report')
    os.environ['KURTEX_PUBLIC_URL'] = 'https://example.up.railway.app'
    report_ctx = Context()
    rq = Query('solve|report')
    state = await report.cb_report_entry(Update(query=rq), report_ctx)
    assert state == report.ASK_TYPE
    assert 'Select vehicle type' in rq.edits[-1][0]
    assert 'web_app' not in str(rq.edits[-1][1])
    assert report_ctx.user_data['report_case_id'] == 'report'

    # No parts replaced: not closed until the user answers.
    new_case('no-parts')
    ctx = Context()
    q = Query('close_confirm|no-parts')
    await agent.cb_close_confirm(Update(query=q), ctx)
    assert store.get_case('no-parts')['status'] == 'assigned'
    assert 'Were any parts replaced' in q.edits[-1][0]
    await agent.cb_close_parts_no(Update(query=Query('close_parts_no|no-parts')), ctx)
    assert store.get_case('no-parts')['status'] == 'done'
    assert store.get_case('no-parts')['parts_replaced'] is False
    assert store.get_case('no-parts')['replaced_parts'] == []

    # Yes -> free text, two part numbers; durable record and no premature close.
    new_case('with-parts')
    ctx = Context()
    await agent.cb_close_confirm(Update(query=Query('close_confirm|with-parts')), ctx)
    q = Query('close_parts_yes|with-parts')
    await agent.cb_close_parts_yes(Update(query=q), ctx)
    assert store.get_case('with-parts')['status'] == 'assigned'
    assert 'Part numbers replaced' in q.edits[-1][0]
    await agent.recv_close_parts_text(Update(message=Message('AB-12345, 456789')), ctx)
    saved = store.get_case('with-parts')
    assert saved['status'] == 'done' and saved['parts_replaced'] is True
    assert saved['replaced_parts'] == ['AB-12345', '456789']
    assert saved['replaced_part_numbers'] == 'AB-12345, 456789'
    assert 'close_parts_case_id' not in ctx.user_data

    # Repeat close cannot replace original parts with new data.
    again = store.close_case('with-parts', replaced_parts=['WRONG'], parts_replaced=True)
    assert again['replaced_parts'] == ['AB-12345', '456789']

    # Yes but part number unavailable -> explicitly record yes with no invented number.
    new_case('unknown-parts')
    ctx = Context()
    await agent.cb_close_confirm(Update(query=Query('close_confirm|unknown-parts')), ctx)
    await agent.cb_close_parts_skip(Update(query=Query('close_parts_skip|unknown-parts')), ctx)
    assert store.get_case('unknown-parts')['parts_replaced'] is True
    assert store.get_case('unknown-parts')['replaced_parts'] == []

    # Cancel leaves the case open and clears pending text.
    new_case('cancelled')
    ctx = Context()
    await agent.cb_close_confirm(Update(query=Query('close_confirm|cancelled')), ctx)
    await agent.cb_close_parts_cancel(Update(query=Query('close_parts_cancel|cancelled')), ctx)
    assert store.get_case('cancelled')['status'] == 'assigned'
    assert 'close_parts_case_id' not in ctx.user_data

    # Invalid input must not close or corrupt history.
    new_case('invalid')
    ctx = Context()
    await agent.cb_close_confirm(Update(query=Query('close_confirm|invalid')), ctx)
    m = Message('A'*81)
    await agent.recv_close_parts_text(Update(message=m), ctx)
    assert store.get_case('invalid')['status'] == 'assigned'
    assert m.replies

    # Search the AI's actual SQLite fleet index for an exact replaced part number.
    index = knowledge.FleetKnowledgeStore(str(store.DATA_DIR / 'fleet-test.sqlite'))
    result = index.ingest_cases([store.get_case('with-parts'), store.get_case('no-parts')])
    assert result['changed'] == 2
    matches = index.matches('AB-12345')
    assert matches and matches[0]['replaced_parts'] == ['AB-12345', '456789']

    print('PASS: Telegram chat report, yes/no/unknown/cancel, input validation, atomic persistence, idempotency, AI part-number retrieval')

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as folder:
        os.environ['DATA_DIR'] = folder
        os.environ['BOT_TOKEN'] = 'test-token-not-real'
        from backend.storage import case_store
        from bot_app.handlers import agent_handler, report_handler
        from backend.ai import fleet_knowledge
        asyncio.run(run_checks(case_store, agent_handler, report_handler, fleet_knowledge))
