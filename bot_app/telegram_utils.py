"""Shared, rate-limit-safe Telegram messaging and HTML escaping helpers."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
from datetime import datetime
from typing import Optional

from telegram.constants import ParseMode
import telegram.error as _tg_error


class _MissingTgError(Exception):
    pass


TelegramError = getattr(_tg_error, "TelegramError", Exception)
BadRequest = getattr(_tg_error, "BadRequest", _MissingTgError)
NetworkError = getattr(_tg_error, "NetworkError", _MissingTgError)
RetryAfter = getattr(_tg_error, "RetryAfter", _MissingTgError)
TimedOut = getattr(_tg_error, "TimedOut", _MissingTgError)

from backend.core.app_time import parse_timestamp, utc_now

_HTML_MODE = getattr(ParseMode, "HTML", "HTML")

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def esc_html(value, fallback: str = "—") -> str:
    """Escape text for Telegram HTML parse mode."""
    if value is None:
        return html.escape(fallback, quote=False)
    text = str(value)
    if not text.strip():
        return html.escape(fallback, quote=False)
    return html.escape(text, quote=False)


def strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or ""))


def truncate_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def format_age_short(iso_str: Optional[str]) -> str:
    dt = parse_timestamp(iso_str)
    if not dt:
        return ""
    secs = max(0, int((utc_now() - dt).total_seconds()))
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        rem = mins % 60
        return f"{hours}h {rem}m ago" if rem else f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


async def safe_answer(query, text: Optional[str] = None, *, show_alert: bool = False, **kwargs) -> bool:
    if query is None:
        return False
    try:
        await query.answer(text=text, show_alert=show_alert, **kwargs)
        return True
    except TelegramError as exc:
        logger.debug("Callback answer ignored: %s", exc)
        return False


@contextlib.asynccontextmanager
async def concurrent_ack(query, text: Optional[str] = None, *, show_alert: bool = False, **kwargs):
    """Fire query.answer concurrently with DB work and edits to halve callback RTT."""
    if query is None:
        yield None
        return
    task = asyncio.create_task(safe_answer(query, text=text, show_alert=show_alert, **kwargs))
    try:
        yield task
    finally:
        try:
            await task
        except Exception as exc:
            logger.debug("Concurrent ack ignored: %s", exc)


async def _call_with_retry(coro_factory, *, plain_fallback_factory=None, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except RetryAfter as exc:
            wait = min(float(exc.retry_after) + 0.5, 30.0)
            logger.warning("Telegram RetryAfter %.1fs (attempt %d/%d)", wait, attempt + 1, max_retries)
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(wait)
        except BadRequest as exc:
            msg = str(exc).lower()
            if "message is not modified" in msg:
                return None
            if plain_fallback_factory is not None and (
                "can't parse entities" in msg or "parse" in msg or "entity" in msg
            ):
                logger.warning("Telegram HTML parse fallback triggered: %s", exc)
                return await plain_fallback_factory()
            raise
        except (TimedOut, NetworkError) as exc:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))


async def safe_send_message(
    bot,
    chat_id: int | str,
    text: str,
    *,
    parse_mode: Optional[str] = _HTML_MODE,
    reply_markup=None,
    max_retries: int = 3,
    **kwargs,
):
    payload = truncate_text(text, 4000)

    async def _send():
        return await bot.send_message(
            chat_id,
            payload,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            **kwargs,
        )

    async def _plain():
        return await bot.send_message(
            chat_id,
            truncate_text(strip_html(payload), 4000),
            parse_mode=None,
            reply_markup=reply_markup,
            **kwargs,
        )

    return await _call_with_retry(_send, plain_fallback_factory=_plain if parse_mode else None, max_retries=max_retries)


async def safe_reply_text(
    message,
    text: str,
    *,
    parse_mode: Optional[str] = _HTML_MODE,
    reply_markup=None,
    max_retries: int = 3,
    **kwargs,
):
    payload = truncate_text(text, 4000)

    async def _send():
        return await message.reply_text(
            payload,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            **kwargs,
        )

    async def _plain():
        return await message.reply_text(
            truncate_text(strip_html(payload), 4000),
            parse_mode=None,
            reply_markup=reply_markup,
            **kwargs,
        )

    return await _call_with_retry(_send, plain_fallback_factory=_plain if parse_mode else None, max_retries=max_retries)


async def safe_edit_message_text(
    query,
    text: str,
    *,
    parse_mode: Optional[str] = _HTML_MODE,
    reply_markup=None,
    max_retries: int = 3,
    **kwargs,
):
    payload = truncate_text(text, 4000)

    async def _edit():
        return await query.edit_message_text(
            payload,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            **kwargs,
        )

    async def _plain():
        return await query.edit_message_text(
            truncate_text(strip_html(payload), 4000),
            parse_mode=None,
            reply_markup=reply_markup,
            **kwargs,
        )

    return await _call_with_retry(_edit, plain_fallback_factory=_plain if parse_mode else None, max_retries=max_retries)


async def safe_delete_message(bot, chat_id: int | str, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except RetryAfter as exc:
        await asyncio.sleep(min(float(exc.retry_after) + 0.5, 10.0))
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramError:
            pass
    except TelegramError:
        pass


async def delete_messages_batch(bot, chat_id: int | str, message_ids: list[int]) -> None:
    if not message_ids:
        return
    sem = asyncio.Semaphore(5)

    async def _del(mid: int):
        async with sem:
            await safe_delete_message(bot, chat_id, mid)

    await asyncio.gather(*(_del(mid) for mid in message_ids), return_exceptions=True)
