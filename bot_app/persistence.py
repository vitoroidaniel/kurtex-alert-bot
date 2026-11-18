"""Keep PTB's existing report-session format without disk I/O on the bot loop."""

from __future__ import annotations

import asyncio
import os
from functools import wraps

from telegram.ext import PicklePersistence


def _offload(method):
    @wraps(method)
    async def wrapper(self, *args, **kwargs):
        # PTB 21.6's PicklePersistence methods are async interfaces around blocking
        # pickle/file operations. Run the whole operation in a worker, serially,
        # so its shared dictionaries and session file cannot be written in parallel.
        async with self._io_lock:
            def run():
                return asyncio.run(method(self, *args, **kwargs))

            task = asyncio.create_task(asyncio.to_thread(run))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Cancelling to_thread cannot stop its write. Wait before releasing
                # the lock, otherwise a following write could overlap that worker.
                try:
                    await task
                finally:
                    raise

    return wrapper


class ThreadedPicklePersistence(PicklePersistence):
    """Serialize persistence operations off-loop; existing drafts load unchanged."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._io_lock = asyncio.Lock()

    def _dump_singlefile(self):
        original = self.filepath
        temporary = original.with_name(f".{original.name}.{os.getpid()}.tmp")
        try:
            self.filepath = temporary
            super()._dump_singlefile()
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, original)
        finally:
            self.filepath = original
            temporary.unlink(missing_ok=True)

    get_user_data = _offload(PicklePersistence.get_user_data)
    get_chat_data = _offload(PicklePersistence.get_chat_data)
    get_bot_data = _offload(PicklePersistence.get_bot_data)
    get_callback_data = _offload(PicklePersistence.get_callback_data)
    get_conversations = _offload(PicklePersistence.get_conversations)
    update_user_data = _offload(PicklePersistence.update_user_data)
    update_chat_data = _offload(PicklePersistence.update_chat_data)
    update_bot_data = _offload(PicklePersistence.update_bot_data)
    update_callback_data = _offload(PicklePersistence.update_callback_data)
    update_conversation = _offload(PicklePersistence.update_conversation)
    drop_user_data = _offload(PicklePersistence.drop_user_data)
    drop_chat_data = _offload(PicklePersistence.drop_chat_data)
    flush = _offload(PicklePersistence.flush)
