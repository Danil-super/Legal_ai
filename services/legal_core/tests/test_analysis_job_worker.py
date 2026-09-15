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


def test_unknown_date_failure_keeps_actionable_error_code(monkeypatch):
    from legal_core import analysis_job_worker as worker
    from legal_core.case_api import ApiError

    finished = []
    job = ClaimedJob(uuid4(), uuid4(), uuid4(), uuid4(), 1, uuid4())

    async def execute(*_args):
        raise ApiError(status_code=422, code="ANALYSIS_DATE_UNCERTAIN", message="Clarify date")

    async def finish(_sessions, item, error):
        finished.append((item.id, error))

    monkeypatch.setattr(worker, "execute_job", execute)
    monkeypatch.setattr(worker, "finish_job", finish)
    asyncio.run(worker.process_job(None, job, None, SimpleNamespace()))
    assert finished == [(job.id, "ANALYSIS_DATE_UNCERTAIN")]


def test_committed_blocked_analysis_is_a_completed_job_even_after_provider_timeout(monkeypatch):
    from legal_core import analysis_job_worker as worker

    claimed = ClaimedJob(uuid4(), uuid4(), uuid4(), uuid4(), 1, uuid4())
    stored = SimpleNamespace(
        state="RUNNING", lease_token=claimed.lease_token, lease_until=object(), error_code=None
    )

    class Session:
        committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def execute(self, *_args):
            pass

        async def scalar(self, *_args):
            return stored

        async def commit(self):
            self.committed = True

    async def blocked_result(_session, _job):
        return SimpleNamespace(analysis_allowed=False, risk_level="UNAVAILABLE")

    monkeypatch.setattr(worker, "job_result", blocked_result)
    session = Session()
    asyncio.run(worker.finish_job(lambda: session, claimed, "ANALYSIS_SERVICE_UNAVAILABLE"))
    assert stored.state == "SUCCEEDED"
    assert stored.error_code is None
    assert stored.lease_token is stored.lease_until is None
    assert session.committed
