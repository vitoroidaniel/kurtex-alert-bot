"""Telegram conversation ordering and slow callback acknowledgement regressions."""

import asyncio
import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.error import NetworkError
from telegram.ext import Application, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
from telegram.request import BaseRequest

from bot_app import telegram_utils
from bot_app.handlers import report_handler
from bot_app.update_processor import PerUserUpdateProcessor


class OfflineRequest(BaseRequest):
    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        if not url.endswith("/getMe"):
            raise AssertionError(f"Unexpected Telegram call: {url.rsplit('/', 1)[-1]}")
        return 200, json.dumps({"ok": True, "result": {
            "id": 999, "is_bot": True, "first_name": "Test", "username": "test_bot",
        }}).encode()


class TelegramUpdateProcessorTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_steps_do_not_block_another_user(self):
        processor = PerUserUpdateProcessor(max_active=2)
        release = asyncio.Event()
        entered = asyncio.Event()
        events = []

        async def slow():
            events.append("first")
            entered.set()
            await release.wait()
            events.append("first finished")

        async def later(n):
            events.append(n)

        user = SimpleNamespace(effective_user=SimpleNamespace(id=1))
        tasks = [asyncio.create_task(processor.process_update(user, slow()))]
        await entered.wait()
        # More waiting steps than the active-work limit must not stall user 2.
        tasks.extend(asyncio.create_task(processor.process_update(user, later(n))) for n in range(20))
        try:
            await asyncio.wait_for(processor.process_update(
                SimpleNamespace(effective_user=SimpleNamespace(id=2)), later("other user")), 1)
            self.assertEqual(events, ["first", "other user"])
        finally:
            release.set()
            await asyncio.gather(*tasks)
        self.assertEqual(events[2:], ["first finished", *range(20)])
        self.assertEqual(processor._users, {})

    async def test_failed_and_cancelled_steps_release_user_lock(self):
        processor = PerUserUpdateProcessor()
        update = SimpleNamespace(effective_user=SimpleNamespace(id=1))

        async def fail():
            raise RuntimeError("handler failure")

        with self.assertRaises(RuntimeError):
            await processor.process_update(update, fail())
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold():
            entered.set()
            await release.wait()

        active = asyncio.create_task(processor.process_update(update, hold()))
        await entered.wait()
        waiting_coroutine = AsyncMock()()
        queued = asyncio.create_task(processor.process_update(update, waiting_coroutine))
        await asyncio.sleep(0)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.assertIsNone(waiting_coroutine.cr_frame)
        release.set()
        await active
        next_step = AsyncMock()
        await processor.process_update(update, next_step())
        next_step.assert_awaited_once()
        self.assertEqual(processor._users, {})

    async def test_slow_ack_is_cancelled_without_losing_handler_result(self):
        cancelled = asyncio.Event()

        async def stalled_answer(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(telegram_utils, "CALLBACK_ACK_TIMEOUT", 0.02):
            async def handler():
                async with telegram_utils.concurrent_ack(SimpleNamespace(answer=stalled_answer)):
                    return report_handler.ASK_UNIT

            self.assertEqual(await asyncio.wait_for(handler(), 1), report_handler.ASK_UNIT)
        self.assertTrue(cancelled.is_set())

    async def test_real_report_conversation_accepts_fast_reply_during_slow_ack(self):
        processor = PerUserUpdateProcessor()
        app = (Application.builder().token("123456:TESTTOKEN").updater(None)
               .request(OfflineRequest()).concurrent_updates(processor).build())
        conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(report_handler.cb_type, pattern=r"^rpt_type\|")],
            states={
                report_handler.ASK_UNIT: [MessageHandler(filters.TEXT, report_handler.recv_unit)],
                report_handler.ASK_DRIVER: [MessageHandler(filters.TEXT, report_handler.recv_driver)],
            },
            fallbacks=[],
            per_chat=False,
        )
        app.add_handler(conv)
        user = User(1, "Agent", False)
        chat = Chat(1, "private")
        message = Message(1, datetime.now(timezone.utc), chat, from_user=user, text="123")
        query = CallbackQuery("test-query", user, "test-chat", message=message, data="rpt_type|truck")
        button_update = Update(1, callback_query=query)
        reply_update = Update(2, message=message)
        prompt_sent = asyncio.Event()

        async def prompt(*args, **kwargs):
            prompt_sent.set()

        async def stalled_answer(*args, **kwargs):
            await asyncio.Event().wait()

        async with app:
            app.user_data[1].update(report_case_id="CASE-1", report_handler="Agent")
            with (patch.object(telegram_utils, "CALLBACK_ACK_TIMEOUT", 0.05),
                  patch.object(CallbackQuery, "answer", new=stalled_answer),
                  patch.object(report_handler, "async_get_case", new=AsyncMock(return_value={"status": "assigned"})),
                  patch.object(report_handler, "safe_edit_message_text", new=prompt),
                  patch.object(report_handler, "safe_reply_text", new=AsyncMock()) as reply):
                first = asyncio.create_task(processor.process_update(button_update, app.process_update(button_update)))
                await asyncio.wait_for(prompt_sent.wait(), 1)
                # User replies as soon as the prompt appears, while answer() is stuck.
                second = asyncio.create_task(processor.process_update(reply_update, app.process_update(reply_update)))
                await asyncio.wait_for(asyncio.gather(first, second), 1)
                self.assertEqual(app.user_data[1]["report"]["unit_number"], "123")
                self.assertEqual(conv._conversations[(1,)], report_handler.ASK_DRIVER)
                reply.assert_awaited_once_with(message, "Driver:", parse_mode=None)

    async def test_double_send_click_delivers_and_saves_report_once(self):
        processor = PerUserUpdateProcessor()
        ctx = SimpleNamespace(
            user_data={"report": {"vehicle_type": "truck", "unit_number": "123"}, "report_case_id": "CASE-1"},
            bot_data={"busy_agents": {1}}, bot=object(),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            callback_query=SimpleNamespace(data="rpt_confirm|yes", answer=AsyncMock()),
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def send(*args, **kwargs):
            entered.set()
            await release.wait()

        with (patch.object(report_handler, "config", SimpleNamespace(REPORTS_GROUP_ID=-100)),
              patch.object(report_handler, "safe_send_message", new=AsyncMock(side_effect=send)) as sent,
              patch.object(report_handler, "async_save_maintenance_report", new=AsyncMock(return_value=True)) as saved,
              patch.object(report_handler, "safe_edit_message_text", new=AsyncMock())):
            first = asyncio.create_task(processor.process_update(update, report_handler.cb_confirm(update, ctx)))
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(processor.process_update(update, report_handler.cb_confirm(update, ctx)))
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 1)
            sent.assert_awaited_once()
            saved.assert_awaited_once()
            self.assertNotIn("report", ctx.user_data)
            self.assertNotIn(1, ctx.bot_data["busy_agents"])

    async def test_failed_report_send_preserves_draft_for_retry(self):
        draft = {"vehicle_type": "truck", "unit_number": "123"}
        ctx = SimpleNamespace(user_data={"report": draft, "report_case_id": "CASE-1"}, bot_data={}, bot=object())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            callback_query=SimpleNamespace(data="rpt_confirm|yes", answer=AsyncMock()),
        )
        with (patch.object(report_handler, "config", SimpleNamespace(REPORTS_GROUP_ID=-100)),
              patch.object(report_handler, "safe_send_message", new=AsyncMock(side_effect=NetworkError("unavailable"))),
              patch.object(report_handler, "async_save_maintenance_report", new=AsyncMock()) as saved,
              patch.object(report_handler, "safe_edit_message_text", new=AsyncMock())):
            self.assertEqual(await report_handler.cb_confirm(update, ctx), report_handler.CONFIRM)
            self.assertIs(ctx.user_data["report"], draft)
            self.assertEqual(ctx.user_data["report_case_id"], "CASE-1")
            saved.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
