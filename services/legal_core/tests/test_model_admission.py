"""Understanding and existing analysis share two slots, never two pools."""

import asyncio
from types import SimpleNamespace

import pytest

from legal_core import analysis_job_worker as worker
from legal_core.model_admission import ModelAdmission


def test_try_admission_is_bounded_without_waiting_and_cancel_releases_capacity():
    async def scenario():
        admission = ModelAdmission()
        assert await admission.try_acquire()
        assert await admission.try_acquire()
        assert not await asyncio.wait_for(admission.try_acquire(), timeout=0.01)
        admission.release()
        entered = asyncio.Event()

        async def task():
            async with admission.slot():
                entered.set()
                await asyncio.Event().wait()
        pending = asyncio.create_task(task())
        await entered.wait()
        assert not await admission.try_acquire()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert await admission.try_acquire()
        admission.release()
        admission.release()
        with pytest.raises(ValueError):
            admission.release()
    asyncio.run(scenario())


def test_worker_waits_for_shared_admission_before_claiming_and_shutdown_releases(monkeypatch):
    async def scenario():
        admission = ModelAdmission()
        assert await admission.try_acquire()  # One running understanding operation.
        entered, stop = asyncio.Event(), asyncio.Event()
        active = maximum = calls = 0

        async def once(*_args):
            nonlocal active, maximum, calls
            calls += 1
            active += 1
            maximum = max(maximum, active)
            entered.set()
            try:
                await stop.wait()
            finally:
                active -= 1
            return False

        monkeypatch.setattr(worker, "worker_once", once)
        task = asyncio.create_task(worker.run_analysis_worker(
            None, SimpleNamespace(), admission=admission,
        ))
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0.01)
        assert calls == maximum == active == 1
        assert not await admission.try_acquire()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0
        assert await admission.try_acquire()
        assert not await admission.try_acquire()
        admission.release()
        admission.release()
    asyncio.run(scenario())
