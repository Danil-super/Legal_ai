import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

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


@pytest.mark.parametrize("kind", ["legacy", "READY", "LIMITED", "NEEDS_FACTS",
                                  "HUMAN_REVIEW-flag-disabled", "HUMAN_REVIEW-version-changed"])
def test_retained_other_results_do_not_restart_agent_even_with_draft_flag(monkeypatch, kind):
    import httpx

    from legal_core import analysis_job_worker as worker
    from legal_core.analysis_contracts import AnalysisSubmissionResponse, PreparationResult
    from test_analysis_api import _report_response
    from test_case_preparation_contracts import _human_review, _limited, _needs_facts, _ready

    monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "true")
    claimed = ClaimedJob(uuid4(), uuid4(), uuid4(), uuid4(), 1, uuid4())
    actor = SimpleNamespace(clinic_id=claimed.clinic_id,
                            membership_id=claimed.actor_membership_id)
    if kind == "legacy":
        result = AnalysisSubmissionResponse(analysisAllowed=True, riskLevel="LOW",
                                           escalationRequired=False, report=_report_response())
    elif kind.startswith("HUMAN_REVIEW"):
        result = PreparationResult.model_validate(_human_review())
    else:
        result = PreparationResult.model_validate({
            "READY": _ready, "LIMITED": _limited, "NEEDS_FACTS": _needs_facts,
        }[kind]())

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, *_args):
            return SimpleNamespace(result_version=1 if kind == "legacy" else 2)

    async def current_actor(*_args):
        return actor

    async def current_case(*_args):
        return SimpleNamespace(retention_due_at=None)

    async def retained(*_args):
        return result

    async def forbidden_lease(*_args):
        if kind.startswith("HUMAN_REVIEW"):
            if kind.endswith("flag-disabled"):
                monkeypatch.delenv("AI_FIRST_LAWYER_DRAFT_ENABLED")
            return SimpleNamespace(result_version=1 if kind.endswith("version-changed") else 2)
        pytest.fail("old retained-result replay must return before requiring a live lease")

    monkeypatch.setattr(worker, "resolve_actor", current_actor)
    monkeypatch.setattr(worker, "_tenant_case", current_case)
    monkeypatch.setattr(worker, "job_result", retained)
    monkeypatch.setattr(worker, "require_job_lease", forbidden_lease)

    def forbidden_http(_request):
        pytest.fail("a retained completed report or clarification must not restart Agent")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden_http)) as client:
            await worker.execute_job(Session, claimed, client,
                                     worker.WorkerSettings("http://agent", "x" * 32))

    asyncio.run(scenario())
