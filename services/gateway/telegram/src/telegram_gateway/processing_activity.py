"""Best-effort native Telegram activity, without chat messages or factual content."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
from typing import Any

from telegram.error import RetryAfter, TelegramError

TYPING_INTERVAL = 4.0  # Telegram clears the action after at most five seconds.


@dataclass
class TypingPulse:
    next_at: float = 0.0

    async def send(self, bot: Any, actor: int) -> None:
        if actor <= 0 or monotonic() < self.next_at:
            return
        self.next_at = monotonic() + TYPING_INTERVAL
        try:
            async with asyncio.timeout(1):
                await bot.send_chat_action(
                    chat_id=actor, action="typing", read_timeout=1, write_timeout=1,
                    connect_timeout=1, pool_timeout=1,
                )
        except RetryAfter as error:
            seconds = error.retry_after.total_seconds() \
                if isinstance(error.retry_after, timedelta) else error.retry_after
            self.next_at = monotonic() + max(TYPING_INTERVAL, seconds)
        except (TelegramError, TimeoutError):
            pass  # A cosmetic API failure must never change a model operation or its result.
