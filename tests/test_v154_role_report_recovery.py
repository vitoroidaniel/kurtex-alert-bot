"""Offline regression for role-change writes, Telegram button feedback and report recovery."""
import asyncio
import os
import runpy
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
_tmp=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=_tmp.name
os.environ['BOT_TOKEN']='test-token-not-real'
# Install the same lightweight Telegram stubs as existing bot regression tests.
runpy.run_path(str(Path(__file__).with_name('test_v146.py')),run_name='telegram_stub_setup')
from backend.storage import user_store,case_store
from bot_app.handlers import report_handler as report


def update(data):
    q=SimpleNamespace(data=data,answer=AsyncMock(),edit_message_text=AsyncMock(),from_user=SimpleNamespace(id=42))
    return SimpleNamespace(callback_query=q,effective_user=SimpleNamespace(id=42,first_name='Agent',last_name='Test')),q

async def run():
    assert user_store.add_user(42,'Agent Test','old','agent')
    case_store.create_case('case-a','Driver A',None,'#repairs','Air leak')
    case_store.assign_case('case-a',42,'Agent Test','old')
    case_store.create_case('case-b','Driver B',None,'#repairs','Tire damage')
    case_store.assign_case('case-b',42,'Agent Test','old')
    ctx=SimpleNamespace(user_data={},bot_data={})
    upd,q=update('solve|case-a')
    assert await report.cb_report_entry(upd,ctx)==report.ASK_TYPE
    assert user_store.edit_user(42,'Agent Test','old','super_admin')
    assert user_store.is_authorized(42)
    upd,q=update('rpt_type|truck')
    assert await report.cb_type(upd,ctx)==report.ASK_UNIT
    assert ctx.user_data['report_case_id']=='case-a'
    # A new report click should not get stuck behind an abandoned draft.
    upd,q=update('solve|case-b')
    assert await report.cb_report_entry(upd,ctx)==report.ASK_TYPE
    assert ctx.user_data['report_case_id']=='case-b'
    assert 'report' not in ctx.user_data
    # An old vehicle keyboard without a case must not open a phantom report.
    ctx.user_data.clear()
    upd,q=update('rpt_type|trailer')
    assert await report.cb_type(upd,ctx)==report.ConversationHandler.END
    assert 'expired' in q.edit_message_text.await_args.args[0]
    upd,q=update('rpt_priority|high')
    await report.cb_orphan_report_callback(upd,ctx)
    assert q.answer.await_count==1
    assert q.answer.await_args.kwargs['show_alert'] is True
    # Dashboard writes and live username refreshes must preserve role and identity.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[]
        for i in range(100):
            futures.append(pool.submit(user_store.sync_telegram_username,42,'updated'))
            futures.append(pool.submit(user_store.edit_user,42,'Agent Test','updated','super_admin'))
        for future in futures:future.result()
    assert user_store.get_user(42)['role']=='super_admin'
    assert user_store.get_user(42)['username']=='updated'
    assert user_store.get_user(42)['name']=='Agent Test'
    # The bot must still recognize the user after a second role transition.
    assert user_store.edit_role(42,'developer')
    assert user_store.is_authorized(42)
    # Developer deliberately excluded from automatic alerts, not from bot access.
    assert 42 not in user_store.get_alert_recipients()
    assert user_store.edit_role(42,'agent')
    assert 42 in user_store.get_alert_recipients()
    # Assert report conversation is named for durable persistence.
    source=(ROOT/'bot_app/handlers/report_handler.py').read_text()
    assert 'name="kurtex_telegram_report_v1"' in source and 'persistent=True' in source
    bot_source=(ROOT/'bot.py').read_text()
    assert '.persistence(report_persistence)' in bot_source
    assert 'telegram_report_sessions.pickle' in bot_source
    print('PASS: role changes, concurrent writes, report switching, expired callback feedback, durable session wiring')

if __name__=='__main__':asyncio.run(run())
