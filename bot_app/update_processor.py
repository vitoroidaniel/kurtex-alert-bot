"""Keep each user's conversations ordered while serving other users in parallel."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from contextlib import asynccontextmanager

from telegram.ext import BaseUpdateProcessor

logger = logging.getLogger(__name__)


class PerUserUpdateProcessor(BaseUpdateProcessor):
    """Serialize user_data/conversation changes, with a separate active-work limit.

    Waiting steps for one user don't consume active handler slots. The larger
    admission limit still bounds how many updates enter this processor at once.
    """

    def __init__(self, max_active: int = 16, max_pending: int = 256):
        if max_active < 1 or max_pending < max_active:
            raise ValueError("Require 1 <= max_active <= max_pending")
        super().__init__(max_pending)
        self._active = asyncio.Semaphore(max_active)
        self._background = asyncio.Semaphore(4)
        self._users = {}

    async def initialize(self):
        pass

    async def shutdown(self):
        # Application.stop drains update tasks before shutting down the processor.
        self._users.clear()

    @asynccontextmanager
    async def _ordered(self, key):
        entry = self._users.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if not entry[1]:
                del self._users[key]

    async def do_process_update(self, update, coroutine):
        user = getattr(update, "effective_user", None)
        chat = getattr(update, "effective_chat", None)
        key = ("user", user.id) if user else ("chat", chat.id) if chat else None
        started = False
        queued_at = time.monotonic()
        try:
            async with self._ordered(key):
                slots = self._background if getattr(chat, "type", None) in ("group", "supergroup", "channel") else self._active
                async with slots:
                    started_at = time.monotonic()
                    if started_at - queued_at > 2:
                        logger.warning("Telegram update %s queued for %.1fs", getattr(update, "update_id", None), started_at - queued_at)
                    started = True
                    try:
                        await coroutine
                    finally:
                        elapsed = time.monotonic() - started_at
                        if elapsed > 5:
                            logger.warning("Telegram update %s took %.1fs", getattr(update, "update_id", None), elapsed)
        finally:
            # A cancelled queued update hasn't started its Application coroutine.
            if not started and inspect.iscoroutine(coroutine):
                coroutine.close()
