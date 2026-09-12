"""Bounded, cancellable editor-file delivery independent of sequential conversations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from telegram.ext import Application

logger = logging.getLogger(__name__)
Delivery = Callable[[], Awaitable[None]]
Admission = Literal["STARTED", "DUPLICATE", "FULL", "STOPPED"]


class EditorFileDeliveryQueue:
    """One file in memory at a time; bounded pending editors, no conversation-state writes."""

    def __init__(
        self, application: Application, *, max_pending: int = 4, timeout_seconds: float = 60
    ) -> None:
        if max_pending < 1 or timeout_seconds <= 0:
            raise ValueError("editor delivery bounds must be positive")
        self.application = application
        self.max_pending = max_pending
        self.timeout_seconds = timeout_seconds
        self._pending: dict[int, asyncio.Task[None]] = {}
        self._one_file = asyncio.Semaphore(1)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def submit(self, actor_id: int, deliver: Delivery, on_error: Delivery) -> Admission:
        if not self.application.running:
            return "STOPPED"
        if actor_id in self._pending:
            return "DUPLICATE"
        if len(self._pending) >= self.max_pending:
            return "FULL"
        # Omitting update= intentionally avoids PTB persistence writes to old user/chat state.
        self._pending[actor_id] = self.application.create_task(
            self._run(actor_id, deliver, on_error), name="legal-editor-file-delivery"
        )
        return "STARTED"

    async def _deliver_one(self, deliver: Delivery) -> None:
        async with self._one_file:
            if self.application.running:
                await deliver()

    async def _run(self, actor_id: int, deliver: Delivery, on_error: Delivery) -> None:
        transfer = asyncio.create_task(self._deliver_one(deliver))
        try:
            async with asyncio.timeout(self.timeout_seconds):
                while self.application.running:
                    done, _ = await asyncio.wait({transfer}, timeout=0.25)
                    if done:
                        await transfer
                        return
                # Application.stop flips running before awaiting create_task children. Observing
                # it lets us cancel a stalled HTTP upload without delaying gateway shutdown.
        except Exception as exc:
            transfer.cancel()
            await asyncio.gather(transfer, return_exceptions=True)
            logger.warning("legal editor delivery failed: %s", type(exc).__name__)
            if self.application.running:
                try:
                    async with asyncio.timeout(5):
                        await on_error()
                except Exception as notification_error:
                    logger.warning(
                        "legal editor delivery notice failed: %s", type(notification_error).__name__
                    )
        finally:
            transfer.cancel()
            await asyncio.gather(transfer, return_exceptions=True)
            self._pending.pop(actor_id, None)

    async def drain(self) -> None:
        await asyncio.gather(*tuple(self._pending.values()), return_exceptions=True)
