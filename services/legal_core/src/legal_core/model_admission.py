"""One process-local two-slot budget shared by analysis and intake model work."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ModelAdmission:
    def __init__(self) -> None:
        self._slots = asyncio.BoundedSemaphore(2)

    async def try_acquire(self) -> bool:
        # No await between this check and acquire's nonblocking branch. Semaphore.locked()
        # also respects existing waiters, so optional intake never jumps the analysis queue.
        if self._slots.locked():
            return False
        await self._slots.acquire()
        return True

    def release(self) -> None:
        self._slots.release()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._slots:
            yield
