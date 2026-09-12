import asyncio
from types import SimpleNamespace
from uuid import uuid4

from legal_core.analysis_jobs import ClaimedJob


def test_worker_failure_is_bounded_sanitized_and_does_not_stop_next_job(monkeypatch):
    from legal_core import analysis_job_worker as worker

    processed = []
    failures = []
    job = ClaimedJob(uuid4(), uuid4(), uuid4(), uuid4(), 1, uuid4())

    async def execute(_sessions, item, _client, _settings):
        processed.append(item.id)
        raise RuntimeError("sensitive-text-must-not-be-logged")

    async def finish(_sessions, item, error):
        failures.append((item.id, error))

    monkeypatch.setattr(worker, "execute_job", execute)
    monkeypatch.setattr(worker, "finish_job", finish)

    asyncio.run(worker.process_job(None, job, None, SimpleNamespace()))
    assert processed == [job.id]
    assert failures == [(job.id, "ANALYSIS_INTERNAL_ERROR")]


def test_job_worker_settings_are_disabled_by_default_and_validate_partial_config():
    import pytest
    from legal_core.analysis_job_worker import WorkerSettings

    assert WorkerSettings.load({}) is None
    with pytest.raises(ValueError):
        WorkerSettings.load({"AGENT_ORCHESTRATOR_URL": "http://agent-orchestrator:8001"})


def test_restart_does_not_claim_backlog_until_orchestrator_is_ready(monkeypatch):
    import httpx
    from legal_core import analysis_job_worker as worker

    async def forbidden_claim(_sessions):
        raise AssertionError("backlog must remain queued while orchestrator is starting")

    monkeypatch.setattr(worker, "claim_job", forbidden_claim)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503))
        ) as client:
            assert (
                await worker.worker_once(
                    None, client, worker.WorkerSettings("http://orchestrator", "x" * 32)
                )
                is False
            )

    asyncio.run(scenario())
