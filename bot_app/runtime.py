"""Retry transient initialization failures and detect a blocked asyncio loop."""

import asyncio
import faulthandler
import logging
import os
import threading
import time

from telegram.error import NetworkError
from telegram.ext import Application

logger = logging.getLogger(__name__)


class ResilientApplication(Application):
    async def initialize(self):
        delay = 1
        while True:
            try:
                await super().initialize()
                return
            except NetworkError as exc:
                logger.warning("Telegram initialization %s; retrying in %ss", type(exc).__name__, delay)
                await asyncio.sleep(delay)
                delay = min(30, delay * 2)


def _terminate_stalled_process():
    logger.critical("Bot event loop stalled for 120s; exiting for Railway recovery")
    try:
        faulthandler.dump_traceback(all_threads=True)
    finally:
        os._exit(1)


class EventLoopWatchdog:
    def __init__(self, timeout=120, check_interval=5, on_stall=_terminate_stalled_process):
        self.timeout = timeout
        self.check_interval = check_interval
        self.on_stall = on_stall
        self._last_pulse = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._monitor, name="bot-loop-watchdog", daemon=True)

    def pulse(self):
        self._last_pulse = time.monotonic()

    def _monitor(self):
        while not self._stop.wait(self.check_interval):
            if time.monotonic() - self._last_pulse > self.timeout:
                self.on_stall()
                return

    async def run(self):
        self.pulse()
        self._thread.start()
        try:
            while True:
                self.pulse()
                await asyncio.sleep(self.check_interval)
        finally:
            self._stop.set()
            self._thread.join(timeout=1)
