"""A retained urgent receipt survives optional assistance and worker recovery."""

import os

import httpx
import pytest
from sqlalchemy import text

from legal_core.analysis_job_worker import WorkerSettings, execute_job, process_job
from legal_core.analysis_jobs import claim_job
from legal_core.case_api import ApiError
from test_lawyer_draft_context import isolated_database as isolated_database
from test_lawyer_draft_context import read, routed_case as routed_case, run
from test_verified_copy_analysis_e2e import INTERNAL_KEY

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("outcome,reclaimed", [
    ("success", False), ("success", True), ("unavailable", False), ("timeout", False),
])
def test_crash_after_human_review_receipt_resumes_optional_agent_with_live_headers(
    routed_case, outcome, reclaimed,
):
    _, actor, _, _, case, job, _, _, early, owner, _ = routed_case
    if reclaimed:
        with owner.begin() as connection:
            connection.execute(text("UPDATE analysis_jobs SET lease_until=clock_timestamp()"
                                    "-interval '1 second' WHERE id=:id"), {"id": job.id})
        recovered = run(claim_job)
        assert recovered.id == job.id and recovered.lease_token != job.lease_token
        job = recovered
    calls = []
    with owner.connect() as connection:
        notifications = connection.scalar(text("SELECT count(*) FROM escalation_notifications"))
        receipt = connection.scalar(text("SELECT response_json FROM idempotency_records "
                                         "WHERE resource_type='CASE_PREPARATION'"))

    def agent(request):
        calls.append(request)
        assert request.url.path == f"/v1/cases/{case}/analyze"
        assert request.headers["X-Agent-Internal-Key"] == INTERNAL_KEY
        assert request.headers["X-Telegram-User-Id"] == str(actor)
        assert request.headers["X-Analysis-Result-Version"] == "2"
        assert request.headers["Idempotency-Key"] == str(job.id)
        assert request.headers["X-Analysis-Job-Token"] == str(job.lease_token)
        # The actual Core context accepts the still-running lease at this boundary.
        assert read(routed_case, headers=dict(request.headers)).status_code == 200
        if outcome == "timeout":
            raise httpx.ReadTimeout("synthetic optional phase timeout")
        return httpx.Response(200 if outcome == "success" else 503, json=early)

    async def operation(sessions):
        async with httpx.AsyncClient(transport=httpx.MockTransport(agent)) as client:
            await process_job(sessions, job, client, WorkerSettings("http://agent", INTERNAL_KEY))

    run(operation)
    assert len(calls) == 1
    with owner.connect() as connection:
        stored = connection.execute(text("SELECT state,error_code,lease_token FROM analysis_jobs "
                                         "WHERE id=:id"), {"id": job.id}).one()
        assert tuple(stored) == ("SUCCEEDED", None, None)
        assert connection.scalar(text("SELECT response_json FROM idempotency_records "
                                      "WHERE resource_type='CASE_PREPARATION'")) == receipt
        assert connection.scalar(text("SELECT count(*) FROM escalation_notifications")) == (
            notifications
        )
        assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0


def test_retained_human_review_does_not_restart_agent_when_feature_is_off(
    routed_case, monkeypatch,
):
    monkeypatch.delenv("AI_FIRST_LAWYER_DRAFT_ENABLED")
    calls = []

    async def operation(sessions):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200),
        )) as client:
            await execute_job(sessions, routed_case[5], client,
                              WorkerSettings("http://agent", INTERNAL_KEY))

    run(operation)
    assert calls == []


def test_retained_human_review_requires_current_lease_before_optional_http(routed_case):
    with routed_case[9].begin() as connection:
        connection.execute(text("UPDATE analysis_jobs SET lease_until=clock_timestamp()"
                                "-interval '1 second' WHERE id=:id"),
                           {"id": routed_case[5].id})
    calls = []

    async def operation(sessions):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200),
        )) as client:
            await execute_job(sessions, routed_case[5], client,
                              WorkerSettings("http://agent", INTERNAL_KEY))

    with pytest.raises(ApiError) as error:
        run(operation)
    assert error.value.code == "ANALYSIS_JOB_LEASE_EXPIRED"
    assert calls == []
