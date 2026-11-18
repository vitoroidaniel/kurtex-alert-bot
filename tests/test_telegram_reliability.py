"""Exercise congestion isolation, restart safety, and report delivery failures."""

import asyncio
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from telegram.error import NetworkError
from telegram.ext import ConversationHandler, PicklePersistence
from telegram.ext import Application, PersistenceInput
from telegram.request import HTTPXRequest

import bot
from bot_app.handlers import report_handler
from bot_app.network import InteractiveTelegramRequest, background_telegram
from bot_app.persistence import ThreadedPicklePersistence
from bot_app.update_processor import PerUserUpdateProcessor
from bot_app.runtime import ResilientApplication, EventLoopWatchdog
from test_telegram_update_processor import OfflineRequest


class TelegramReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_requests_cannot_exhaust_interactive_connections(self):
        request = InteractiveTelegramRequest(connection_pool_size=64)
        request._background_interval = 0  # Exercise pool isolation independently of pacing.
        release = asyncio.Event()
        full = asyncio.Event()
        running = 0
        maximum = 0

        async def fake_request(owner, *args, **kwargs):
            nonlocal running, maximum
            if owner is request._background_request:
                running += 1
                maximum = max(maximum, running)
                if running == 8:
                    full.set()
                try:
                    await release.wait()
                finally:
                    running -= 1
            return 200, b'{}'

        @background_telegram
        async def bulk():
            return await request.do_request("https://example.invalid/sendMessage", "POST")

        with patch.object(HTTPXRequest, "do_request", new=fake_request):
            tasks = [asyncio.create_task(bulk()) for _ in range(64)]
            try:
                await asyncio.wait_for(full.wait(), 1)
                result = await asyncio.wait_for(request.do_request("https://example.invalid/sendMessage", "POST"), 1)
                self.assertEqual(result[0], 200)
                self.assertEqual(maximum, 8)
                self.assertTrue(all(not task.done() for task in tasks))
            finally:
                release.set()
                await asyncio.gather(*tasks)
                await request.shutdown()

    async def test_bulk_requests_are_paced_without_delaying_interactive_request(self):
        request = InteractiveTelegramRequest(connection_pool_size=64)
        self.assertEqual(request._background_interval, 0.1)
        request._background_interval = 0.03
        timestamps = []

        async def fake_request(owner, *args, **kwargs):
            if owner is request._background_request:
                timestamps.append(asyncio.get_running_loop().time())
            return 200, b'{}'

        @background_telegram
        async def bulk():
            await request.do_request("https://example.invalid/sendMessage", "POST")

        with patch.object(HTTPXRequest, "do_request", new=fake_request):
            tasks = [asyncio.create_task(bulk()) for _ in range(5)]
            try:
                await asyncio.sleep(0.01)
                await asyncio.wait_for(request.do_request("https://example.invalid/sendMessage", "POST"), 0.05)
                self.assertTrue(any(not task.done() for task in tasks))
                await asyncio.gather(*tasks)
                self.assertTrue(all(b - a >= 0.025 for a, b in zip(timestamps, timestamps[1:])))
            finally:
                await asyncio.gather(*tasks, return_exceptions=True)
                await request.shutdown()

    async def test_group_alert_storm_does_not_take_report_handler_slots(self):
        processor = PerUserUpdateProcessor(max_active=2)
        release = asyncio.Event()
        entered = asyncio.Event()
        running = 0

        async def blocked():
            nonlocal running
            running += 1
            if running == 4:
                entered.set()
            await release.wait()

        tasks = [asyncio.create_task(processor.process_update(
            SimpleNamespace(effective_user=SimpleNamespace(id=i), effective_chat=SimpleNamespace(id=-i, type="supergroup")), blocked(),
        )) for i in range(1, 65)]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            callback = AsyncMock()
            await asyncio.wait_for(processor.process_update(SimpleNamespace(
                effective_user=SimpleNamespace(id=999), effective_chat=SimpleNamespace(id=999, type="private"),
            ), callback()), 1)
            callback.assert_awaited_once()
            self.assertEqual(running, 4)
        finally:
            release.set()
            await asyncio.gather(*tasks)

    async def test_interrupted_session_write_keeps_previous_draft_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.pickle"
            original = PicklePersistence(path)
            await original.update_user_data(1, {"report": {"location": "Saved location"}})
            before = path.read_bytes()
            current = ThreadedPicklePersistence(path)
            with patch("bot_app.persistence.os.replace", side_effect=OSError("Disk unavailable")):
                with self.assertRaises(OSError):
                    await current.update_user_data(1, {"report": {"location": "New location"}})
            self.assertEqual(path.read_bytes(), before)
            restored = await PicklePersistence(path).get_user_data()
            self.assertEqual(restored[1]["report"]["location"], "Saved location")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def report_context(self):
        return SimpleNamespace(
            user_data={"report": {"vehicle_type": "truck", "unit_number": "123"}, "report_case_id": "CASE-1"},
            bot_data={}, bot=object(),
        )

    def report_update(self):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            callback_query=SimpleNamespace(data="rpt_confirm|yes", answer=AsyncMock()),
        )

    async def test_database_failure_retry_does_not_send_report_again(self):
        ctx = self.report_context()
        update = self.report_update()
        with (patch.object(report_handler, "config", SimpleNamespace(REPORTS_GROUP_ID=-100)),
              patch.object(report_handler, "safe_send_message", new=AsyncMock(return_value=SimpleNamespace(message_id=20))) as sent,
              patch.object(report_handler, "async_save_maintenance_report", new=AsyncMock(side_effect=[RuntimeError("DB busy"), True])) as saved,
              patch.object(report_handler, "safe_edit_message_text", new=AsyncMock())):
            self.assertEqual(await report_handler.cb_confirm(update, ctx), report_handler.CONFIRM)
            self.assertIn("report_delivery", ctx.user_data)
            # Simulate restart restoring the same persisted draft and delivery marker.
            ctx.user_data = dict(ctx.user_data)
            self.assertEqual(await report_handler.cb_confirm(update, ctx), ConversationHandler.END)
            sent.assert_awaited_once()
            self.assertEqual(saved.await_count, 2)
            self.assertNotIn("report", ctx.user_data)

    async def test_confirmation_edit_failure_does_not_turn_success_into_failed_send(self):
        ctx = self.report_context()
        with (patch.object(report_handler, "config", SimpleNamespace(REPORTS_GROUP_ID=-100)),
              patch.object(report_handler, "safe_send_message", new=AsyncMock()) as sent,
              patch.object(report_handler, "async_save_maintenance_report", new=AsyncMock(return_value=True)) as saved,
              patch.object(report_handler, "safe_edit_message_text", new=AsyncMock(side_effect=NetworkError("Unavailable")))):
            self.assertEqual(await report_handler.cb_confirm(self.report_update(), ctx), ConversationHandler.END)
            sent.assert_awaited_once()
            saved.assert_awaited_once()
            self.assertNotIn("report", ctx.user_data)

    async def test_delivered_marker_is_on_disk_before_case_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.pickle"
            persistence = ThreadedPicklePersistence(path, store_data=PersistenceInput(
                user_data=True, chat_data=False, bot_data=False, callback_data=False,
            ))
            app = (Application.builder().token("123456:TESTTOKEN").request(OfflineRequest())
                   .updater(None).persistence(persistence).build())
            async with app:
                ctx = self.report_context()
                app.user_data[1].update(ctx.user_data)
                ctx.user_data = app.user_data[1]
                ctx.application = app

                async def save(*args):
                    restored = await PicklePersistence(path).get_user_data()
                    self.assertEqual(restored[1]["report_delivery"]["message_id"], 20)
                    raise RuntimeError("DB unavailable")

                with (patch.object(report_handler, "config", SimpleNamespace(REPORTS_GROUP_ID=-100)),
                      patch.object(report_handler, "safe_send_message", new=AsyncMock(return_value=SimpleNamespace(message_id=20))),
                      patch.object(report_handler, "async_save_maintenance_report", new=save),
                      patch.object(report_handler, "safe_edit_message_text", new=AsyncMock())):
                    self.assertEqual(await report_handler.cb_confirm(self.report_update(), ctx), report_handler.CONFIRM)
            restored = await PicklePersistence(path).get_user_data()
            self.assertIn("report_delivery", restored[1])

    async def test_telegram_rate_limit_wait_is_not_shortened(self):
        from telegram.error import RetryAfter
        from bot_app import telegram_utils
        action = AsyncMock(side_effect=[RetryAfter(45), "sent"])
        with patch.object(telegram_utils.asyncio, "sleep", new=AsyncMock()) as sleep:
            self.assertEqual(await telegram_utils._call_with_retry(action), "sent")
            sleep.assert_awaited_once_with(45.5)

    async def test_startup_does_not_wait_for_command_registration(self):
        blocked = asyncio.Event()
        user = SimpleNamespace(id=999, username="test_bot")
        app = SimpleNamespace(
            bot=SimpleNamespace(bot=user, set_my_commands=AsyncMock(side_effect=lambda *a, **k: None)),
            bot_data={}, updater=SimpleNamespace(running=False),
        )

        async def slow_commands(*args, **kwargs):
            await blocked.wait()

        app.bot.set_my_commands.side_effect = slow_commands
        with (patch.object(bot, "ensure_indexes", new=AsyncMock()),
              patch.object(bot, "_write_bot_heartbeat"),
              patch.object(bot, "stop_api_server")):
            try:
                await asyncio.wait_for(bot.post_init(app), 1)
                self.assertFalse(app.bot_data["commands_task"].done())
            finally:
                await bot.post_shutdown(app)
            self.assertNotIn("commands_task", app.bot_data)
            self.assertNotIn("heartbeat_task", app.bot_data)

    async def test_transient_initialization_failure_retries_until_telegram_recovers(self):
        app = (Application.builder().application_class(ResilientApplication)
               .token("123456:TESTTOKEN").request(OfflineRequest()).get_updates_request(OfflineRequest())
               .updater(None).build())
        with (patch.object(Application, "initialize", new=AsyncMock(side_effect=[NetworkError("Bad Gateway"), NetworkError("timeout"), None])) as initialize,
              patch("bot_app.runtime.asyncio.sleep", new=AsyncMock()) as sleep):
            await app.initialize()
            self.assertEqual(initialize.await_count, 3)
            self.assertEqual([call.args[0] for call in sleep.await_args_list], [1, 2])

    async def test_invalid_token_is_not_retried_forever(self):
        from telegram.error import InvalidToken
        app = (Application.builder().application_class(ResilientApplication)
               .token("123456:TESTTOKEN").request(OfflineRequest()).get_updates_request(OfflineRequest())
               .updater(None).build())
        with patch.object(Application, "initialize", new=AsyncMock(side_effect=InvalidToken("Invalid token"))) as initialize:
            with self.assertRaises(InvalidToken):
                await app.initialize()
            initialize.assert_awaited_once()

    async def test_watchdog_detects_actual_loop_stall(self):
        detected = threading.Event()
        watchdog = EventLoopWatchdog(timeout=0.05, check_interval=0.01, on_stall=detected.set)
        task = asyncio.create_task(watchdog.run())
        await asyncio.sleep(0.01)
        try:
            time.sleep(0.12)  # Deliberately block this test loop; watchdog runs separately.
            self.assertTrue(detected.is_set())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(watchdog._thread.is_alive())

    async def test_watchdog_allows_slow_async_network_wait(self):
        detected = threading.Event()
        watchdog = EventLoopWatchdog(timeout=0.1, check_interval=0.01, on_stall=detected.set)
        task = asyncio.create_task(watchdog.run())
        try:
            await asyncio.sleep(0.2)
            self.assertFalse(detected.is_set())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
