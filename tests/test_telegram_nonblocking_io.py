"""Check report processing while session writes and user lookups are slow."""

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

from telegram.ext import PicklePersistence

import bot
from bot_app.handlers import report_handler
from bot_app.persistence import ThreadedPicklePersistence
from bot_app.update_processor import PerUserUpdateProcessor


class TelegramNonblockingIOTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_drafts_roundtrip_with_concurrent_session_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telegram_report_sessions.pickle"
            original = PicklePersistence(path)
            await original.update_user_data(1, {"report": {"location": "Dortches, NC"}})
            await original.update_conversation("kurtex_telegram_report_v1", (1,), report_handler.ASK_LOCATION)
            persistence = ThreadedPicklePersistence(path)
            users = await persistence.get_user_data()
            self.assertEqual(users[1]["report"]["location"], "Dortches, NC")
            states = await persistence.get_conversations("kurtex_telegram_report_v1")
            self.assertEqual(states[(1,)], report_handler.ASK_LOCATION)
            await asyncio.gather(*(persistence.update_user_data(i, {"report": {"unit_number": str(i)}}) for i in range(2, 25)))
            await persistence.update_conversation("kurtex_telegram_report_v1", (1,), report_handler.ASK_COMMENTS)
            await persistence.flush()
            # File stays readable by the original PTB implementation, including states.
            restored = PicklePersistence(path)
            self.assertEqual(len(await restored.get_user_data()), 24)
            states = await restored.get_conversations("kurtex_telegram_report_v1")
            self.assertEqual(states[(1,)], report_handler.ASK_COMMENTS)

    async def test_100_location_replies_continue_during_slow_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            persistence = ThreadedPicklePersistence(Path(directory) / "sessions.pickle")
            writer_entered = threading.Event()
            lookup_entered = threading.Event()
            release = threading.Event()
            processor = PerUserUpdateProcessor()
            completed = []

            def slow_write(_self):
                writer_entered.set()
                release.wait(3)

            def lookup(user_id):
                if user_id == 999:
                    lookup_entered.set()
                    release.wait(3)
                return {"username": "agent", "role": "agent"}

            def make_update(user_id):
                return SimpleNamespace(
                    update_id=user_id, effective_user=SimpleNamespace(id=user_id, username="agent"),
                    effective_chat=SimpleNamespace(type="private"), callback_query=None,
                    message=SimpleNamespace(text="Dortches, NC"),
                )

            async def location(update):
                ctx = SimpleNamespace(user_data={"report": {
                    "vehicle_type": "reefer", "load_type": "Empty", "load": "Empty",
                    "pickup": "—", "delivery": "—",
                }})
                await bot.auth_middleware(update, ctx)
                state = await report_handler.recv_location(update, ctx)
                self.assertEqual(state, report_handler.ASK_COMMENTS)
                self.assertEqual(ctx.user_data["report"]["location"], "Dortches, NC")
                completed.append(update.effective_user.id)

            with (patch.object(ThreadedPicklePersistence, "_dump_singlefile", new=slow_write),
                  patch.object(bot, "get_user", side_effect=lookup),
                  patch.object(report_handler, "safe_reply_text", new=AsyncMock()) as reply):
                writer = asyncio.create_task(persistence.update_user_data(1, {"report": {}}))
                slow_update = make_update(999)
                blocked_user = asyncio.create_task(processor.process_update(slow_update, bot.auth_middleware(slow_update, None)))
                started = time.monotonic()
                tasks = []
                try:
                    while not (writer_entered.is_set() and lookup_entered.is_set()):
                        self.assertLess(time.monotonic() - started, 1, "Storage blocked the bot event loop")
                        await asyncio.sleep(0.005)
                    for user_id in range(1, 101):
                        update = make_update(user_id)
                        tasks.append(asyncio.create_task(processor.process_update(update, location(update))))
                    await asyncio.wait_for(asyncio.gather(*tasks), 1)
                    self.assertEqual(len(completed), 100)
                    self.assertFalse(writer.done())
                    self.assertFalse(blocked_user.done())
                    self.assertEqual(reply.await_count, 100)
                    for call in reply.await_args_list:
                        self.assertEqual(call.args[1], "Comments:")
                finally:
                    release.set()
                    await asyncio.gather(writer, blocked_user, *tasks, return_exceptions=True)

    async def test_cancelling_session_write_does_not_allow_overlapping_write(self):
        with tempfile.TemporaryDirectory() as directory:
            persistence = ThreadedPicklePersistence(Path(directory) / "sessions.pickle")
            entered = threading.Event()
            release = threading.Event()
            calls = []

            def write(_self):
                calls.append(threading.get_ident())
                if len(calls) == 1:
                    entered.set()
                    release.wait(3)

            with patch.object(ThreadedPicklePersistence, "_dump_singlefile", new=write):
                first = asyncio.create_task(persistence.update_user_data(1, {"a": 1}))
                try:
                    deadline = time.monotonic() + 1
                    while not entered.is_set():
                        self.assertLess(time.monotonic(), deadline)
                        await asyncio.sleep(0.005)
                    first.cancel()
                    second = asyncio.create_task(persistence.update_user_data(2, {"b": 2}))
                    await asyncio.sleep(0.02)
                    self.assertEqual(len(calls), 1)
                finally:
                    release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                await second
                self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
