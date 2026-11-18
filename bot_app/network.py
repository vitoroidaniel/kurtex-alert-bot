"""Reserve Telegram connections for interactive commands and report prompts."""

import asyncio
from contextvars import ContextVar
from functools import wraps

from telegram.request import HTTPXRequest

_BACKGROUND = ContextVar("telegram_background", default=False)


def background_telegram(callback):
    @wraps(callback)
    async def wrapper(*args, **kwargs):
        token = _BACKGROUND.set(True)
        try:
            return await callback(*args, **kwargs)
        finally:
            _BACKGROUND.reset(token)
    return wrapper


class InteractiveTelegramRequest(HTTPXRequest):
    """Bulk traffic has its own pool and cannot occupy report connections."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        background_kwargs = {**kwargs, "connection_pool_size": 8}
        self._background_request = HTTPXRequest(**background_kwargs)
        self._background_slots = asyncio.Semaphore(8)
        self._background_gate = asyncio.Lock()
        self._background_interval = 0.1
        self._next_background_at = 0.0

    async def initialize(self):
        await super().initialize()
        await self._background_request.initialize()

    async def shutdown(self):
        try:
            await super().shutdown()
        finally:
            await self._background_request.shutdown()

    async def do_request(self, url, method, request_data=None, **kwargs):
        if _BACKGROUND.get():
            async with self._background_slots:
                # Reserve dispatch time after acquiring a slot: queued requests
                # must not burst together when a stalled connection is released.
                async with self._background_gate:
                    loop = asyncio.get_running_loop()
                    await asyncio.sleep(max(0, self._next_background_at - loop.time()))
                    self._next_background_at = loop.time() + self._background_interval
                return await self._background_request.do_request(url, method, request_data, **kwargs)
        return await super().do_request(url, method, request_data, **kwargs)
