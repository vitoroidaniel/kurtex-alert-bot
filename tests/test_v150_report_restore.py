"""Offline report flow regression checks based on user-supplied original chat report."""
import asyncio
import os
import runpy
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

# Reuse the existing self-contained Telegram stubs, no token/network required.
_TEST_DATA = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = _TEST_DATA.name
os.environ['BOT_TOKEN'] = 'test-token-not-real'
runpy.run_path(str(Path(__file__).with_name('test_v146.py')), run_name='report_test_stubs')
from bot_app.handlers import report_handler as r


def make_ctx(vehicle='reefer'):
    return SimpleNamespace(user_data={'report': {'vehicle_type': vehicle, 'media': []}}, bot_data={})


def callback_ctx(ctx, value):
    q = SimpleNamespace(data=value, answer=AsyncMock(), edit_message_text=AsyncMock(), from_user=SimpleNamespace(id=123))
    u = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=123))
    return u, q


def message_ctx(value):
    msg = SimpleNamespace(text=value, reply_text=AsyncMock())
    return SimpleNamespace(message=msg), msg


async def run():
    assert [b.text for row in r.LOAD_TYPE_KB.inline_keyboard for b in row] == [
        'JBS Load', 'Broker Load', 'Meijer Load', 'OpenDeck', 'Empty']
    # Every vehicle type + Empty: never ask temperatures, even after Skip.
    for vehicle in ('truck', 'trailer', 'reefer'):
        ctx = make_ctx(vehicle)
        update, q = callback_ctx(ctx, 'rpt_loadtype|empty')
        assert await r.cb_loadtype(update, ctx) == r.ASK_LOCATION
        assert ctx.user_data['report']['load'] == 'Empty'
        assert ctx.user_data['report']['pickup'] == '—'
        assert ctx.user_data['report']['delivery'] == '—'
        assert 'Current Location' in q.edit_message_text.await_args.args[0]
        upd, msg = message_ctx('Houston TX')
        assert await r.recv_location(upd, ctx) == r.ASK_COMMENTS
        assert 'Comments' in msg.reply_text.await_args.args[0]
        preview = r._build_report(ctx.user_data['report'])
        assert 'Setpoint' not in preview and 'Temp recorder' not in preview
        assert 'Pick up Location' not in preview and 'Delivery Location' not in preview
        assert 'Setpoint' not in [b.text for row in r._edit_fields_kb(vehicle, ctx.user_data['report']).inline_keyboard for b in row]

        # Skip location follows the exact same no-temperature route.
        ctx = make_ctx(vehicle)
        upd, _ = callback_ctx(ctx, 'rpt_loadtype|empty')
        await r.cb_loadtype(upd, ctx)
        upd, q = callback_ctx(ctx, 'rpt_skip')
        assert await r.cb_skip(upd, ctx) == r.ASK_COMMENTS
        assert 'Comments' in q.edit_message_text.await_args.args[0]

    # Loaded reefers retain the original temperature steps.
    for choice in ('jbs', 'broker', 'meijer', 'opendeck'):
        ctx = make_ctx('reefer')
        upd, q = callback_ctx(ctx, f'rpt_loadtype|{choice}')
        assert await r.cb_loadtype(upd, ctx) == r.ASK_PICKUP
        assert 'Pick up' in q.edit_message_text.await_args.args[0]
        for name, val, expected in [('recv_pickup','A',r.ASK_DELIVERY),('recv_delivery','B',r.ASK_LOCATION),('recv_location','C',r.ASK_SETPOINT)]:
            upd, msg = message_ctx(val)
            assert await getattr(r, name)(upd, ctx) == expected
        assert 'Setpoint' in msg.reply_text.await_args.args[0]

    # Loaded reefer + Skip Location also requests setpoint.
    ctx = make_ctx('reefer')
    ctx.user_data['report'].update(load='JBS Load', pickup='A', delivery='B')
    upd, q = callback_ctx(ctx, 'rpt_skip')
    assert await r.cb_skip(upd, ctx) == r.ASK_SETPOINT
    assert 'Setpoint' in q.edit_message_text.await_args.args[0]

    # Switching to Empty in Edit clears stale temperatures and hides their fields.
    ctx.user_data['report'].update(setpoint='-10', current_temp='-8', temp_recorder='Y')
    ctx.user_data['editing_field'] = 'load'
    upd, q = callback_ctx(ctx, 'rpt_editval|Empty')
    assert await r.cb_edit_val_button(upd, ctx) == r.CONFIRM
    d = ctx.user_data['report']
    assert all(key not in d for key in ('setpoint', 'current_temp', 'temp_recorder'))
    assert 'Setpoint' not in r._build_report(d)
    assert 'Setpoint' not in [b.text for row in r._edit_fields_kb('reefer', d).inline_keyboard for b in row]

    # Text fallback for load selection is still chat-based and respects Empty.
    ctx = make_ctx('reefer')
    upd, msg = message_ctx('Empty')
    assert await r.recv_load(upd, ctx) == r.ASK_LOCATION
    assert 'Current Location' in msg.reply_text.await_args.args[0]

    # Confirm chat Report entry exists and uses Telegram callback, not WebApp.
    from bot_app.handlers.agent_handler import _active_case_keyboard
    assert _active_case_keyboard('case-1').inline_keyboard[0][1].callback_data == 'solve|case-1'
    assert not any(getattr(b, 'web_app', None) for row in _active_case_keyboard('case-1').inline_keyboard for b in row)
    print('PASS: all five load types, Empty across three vehicles, Skip, loaded reefer, Edit, text fallback, Telegram chat entry')


if __name__ == '__main__':
    asyncio.run(run())
