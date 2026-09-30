"""Bounded Telegram update concurrency without cross-user head-of-line blocking."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from telegram.ext import BaseUpdateProcessor


def _actor_key(update: object) -> int | None:
    """Return a stable actor key without retaining a Telegram update or its content."""

    actor = getattr(update, "effective_user", None)
    value = getattr(actor, "id", None)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


class ActorSerialUpdateProcessor(BaseUpdateProcessor):
    """Process different users concurrently while preserving each user's update order.

    ConversationHandler state is per user, so executing two updates from the same actor in
    parallel can race its state. A process-wide sequential dispatcher avoids that race but
    makes every user wait for a slow Legal Core or Telegram request from someone else.
    """

    def __init__(self, max_concurrent_updates: int, queued_update_limit: int = 64) -> None:
        """Reserve active slots for distinct actors, with a bounded waiting queue."""

        if queued_update_limit < max_concurrent_updates:
            raise ValueError("queued update limit must cover all concurrent actors")
        # BaseUpdateProcessor holds this semaphore before `do_process_update`.  It must be
        # larger than the active-actor limit: an update waiting for its own actor's lock must
        # not consume an active slot that another actor could use.
        super().__init__(max_concurrent_updates=queued_update_limit)
        self._active_actor_semaphore = asyncio.BoundedSemaphore(max_concurrent_updates)
        self._actor_locks: dict[int, asyncio.Lock] = {}
        self._actor_waiters: dict[int, int] = {}

    async def do_process_update(
        self,
        update: object,
        coroutine: Awaitable[Any],
    ) -> None:
        actor_key = _actor_key(update)
        if actor_key is None:
            await coroutine
            return

        lock = self._actor_locks.setdefault(actor_key, asyncio.Lock())
        self._actor_waiters[actor_key] = self._actor_waiters.get(actor_key, 0) + 1
        try:
            async with lock, self._active_actor_semaphore:
                await coroutine
        finally:
            remaining = self._actor_waiters[actor_key] - 1
            if remaining:
                self._actor_waiters[actor_key] = remaining
            else:
                self._actor_waiters.pop(actor_key, None)
                if self._actor_locks.get(actor_key) is lock:
                    self._actor_locks.pop(actor_key, None)

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        self._actor_locks.clear()
        self._actor_waiters.clear()
