"""Bounded durable retry uses real claims, receipts and normal submission fences."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.analysis_job_worker import WorkerSettings, finish_job, process_job
from legal_core.analysis_jobs import claim_job
from legal_core.database import database_url
from test_lawyer_draft_context import routed_case as routed_case
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.fixture
def queued_case(prepared_case):
    client, _, _, case, headers, _, _ = prepared_case
    queued = client.post(f"/v1/cases/{case}/analysis-jobs", headers=headers,
                         json={"messageId": 456})
    assert queued.status_code == 202, queued.text
    return prepared_case, UUID(queued.json()["jobId"])


@asynccontextmanager
async def sessions():
    engine = create_async_engine(database_url())
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def row(prepared, job_id):
    with prepared[-1].connect() as connection:
        return connection.execute(text(
            "SELECT state,attempts,lease_token,lease_until,error_code "
            "FROM analysis_jobs WHERE id=:id"
        ), {"id": job_id}).one()


def submit(prepared, job):
    client, _, _, case, headers, context, _ = prepared
    return client.post(f"/v1/cases/{case}/analysis-submissions", headers=headers | {
        "Idempotency-Key": str(job.id), "X-Analysis-Job-Id": str(job.id),
        "X-Analysis-Job-Token": str(job.lease_token),
    }, json=_submission(context, questions=False, claims=True))


@pytest.mark.parametrize("error", ["ANALYSIS_PROVIDER_UNAVAILABLE", "ANALYSIS_SERVICE_UNAVAILABLE"])
def test_first_transient_requeues_once_and_fences_old_owner(queued_case, caplog, error):
    prepared, job_id = queued_case
    caplog.set_level(logging.INFO, logger="legal_core.analysis_job_worker")

    async def scenario():
        async with sessions() as factory:
            first = await claim_job(factory)
            assert first is not None and first.id == job_id
            assert row(prepared, job_id).attempts == 1
            await finish_job(factory, first, error)
            assert row(prepared, job_id) == ("QUEUED", 1, None, None, None)
            with prepared[-1].connect() as connection:
                assert connection.scalar(text(
                    "SELECT count(*) FROM analysis_job_notifications() WHERE job_id=:id"
                ), {"id": job_id}) == 0
            second = await claim_job(factory)
            assert second is not None and second.id == first.id
            assert second.lease_token != first.lease_token
            assert row(prepared, job_id).attempts == 2
            # Neither a late completion nor a late publication may use the first token.
            await finish_job(factory, first, None)
            assert row(prepared, job_id).lease_token == second.lease_token
            late = await asyncio.to_thread(submit, prepared, first)
            assert late.status_code == 409, late.text
            assert late.json()["error"]["code"] == "ANALYSIS_JOB_LEASE_EXPIRED"
            await finish_job(factory, second, error)
            assert row(prepared, job_id) == ("FAILED", 2, None, None, error)
            assert await claim_job(factory) is None

    asyncio.run(scenario())
    retries = [record.getMessage() for record in caplog.records
               if record.getMessage().startswith("analysis_job_retry_queued")]
    assert retries == [f"analysis_job_retry_queued job={job_id} attempt=1 error={error}"]
    with prepared[-1].connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:case"),
                                 {"case": prepared[3]}) == 0
        assert connection.scalar(text("SELECT count(*) FROM idempotency_records WHERE key=:key"),
                                 {"key": str(job_id)}) == 0


@pytest.mark.parametrize("error", [
    "ACTOR_NOT_AUTHORIZED", "ANALYSIS_DATE_UNCERTAIN", "ANALYSIS_CONTEXT_STALE",
    "ANALYSIS_SAFE_STOP", "ANALYSIS_PROVIDER_INVALID", "ANALYSIS_INTERNAL_ERROR", None,
])
def test_nontransient_or_missing_result_is_terminal(queued_case, error):
    prepared, job_id = queued_case

    async def scenario():
        async with sessions() as factory:
            first = await claim_job(factory)
            assert first is not None
            await finish_job(factory, first, error)
            assert row(prepared, job_id) == (
                "FAILED", 1, None, None, error or "ANALYSIS_RESULT_MISSING",
            )
            assert await claim_job(factory) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("error", ["ANALYSIS_PROVIDER_UNAVAILABLE", "ANALYSIS_SERVICE_UNAVAILABLE"])
def test_retained_result_wins_over_uncertain_http_and_never_requeues(queued_case, error):
    prepared, job_id = queued_case

    async def scenario():
        async with sessions() as factory:
            first = await claim_job(factory)
            assert first is not None
            response = await asyncio.to_thread(submit, prepared, first)
            assert response.status_code == 201, response.text
            await finish_job(factory, first, error)
            assert row(prepared, job_id) == ("SUCCEEDED", 1, None, None, None)
            assert await claim_job(factory) is None

            def forbidden_http(request):
                pytest.fail("retained normal result must remain model-free")

            async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden_http)) as client:
                await process_job(factory, first, client,
                                  WorkerSettings("http://agent", INTERNAL_KEY))

    asyncio.run(scenario())
    with prepared[-1].connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:case"),
                                 {"case": prepared[3]}) == 1
        assert connection.scalar(text("SELECT count(*) FROM idempotency_records WHERE key=:key"),
                                 {"key": str(job_id)}) == 1


def test_process_job_recovers_then_uses_real_committed_result_after_http_timeout(queued_case):
    prepared, job_id = queued_case
    calls = []

    async def scenario():
        async with sessions() as factory:
            first = await claim_job(factory)
            assert first is not None
            current = [first]

            async def orchestrator(request):
                calls.append(dict(request.headers))
                assert request.headers["idempotency-key"] == str(job_id)
                assert request.headers["x-analysis-job-token"] == str(current[0].lease_token)
                if len(calls) == 1:
                    return httpx.Response(503, json={
                        "detail": {"code": "ANALYSIS_PROVIDER_UNAVAILABLE"},
                    })
                committed = await asyncio.to_thread(submit, prepared, current[0])
                assert committed.status_code == 201, committed.text
                raise httpx.ReadTimeout("synthetic uncertain response", request=request)

            async with httpx.AsyncClient(transport=httpx.MockTransport(orchestrator)) as client:
                settings = WorkerSettings("http://agent", INTERNAL_KEY)
                await process_job(factory, first, client, settings)
                assert row(prepared, job_id) == ("QUEUED", 1, None, None, None)
                second = await claim_job(factory)
                assert second is not None and second.lease_token != first.lease_token
                current[0] = second
                await process_job(factory, second, client, settings)
                assert row(prepared, job_id) == ("SUCCEEDED", 2, None, None, None)
                assert await claim_job(factory) is None

    asyncio.run(scenario())
    assert len(calls) == 2
    with prepared[-1].connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:case"),
                                 {"case": prepared[3]}) == 1


def test_existing_three_claim_crash_recovery_budget_is_unchanged(queued_case):
    prepared, job_id = queued_case

    async def scenario():
        async with sessions() as factory:
            tokens = set()
            for attempt in (1, 2, 3):
                claimed = await claim_job(factory)
                assert claimed is not None and claimed.id == job_id
                assert claimed.lease_token not in tokens
                tokens.add(claimed.lease_token)
                assert row(prepared, job_id).attempts == attempt
                with prepared[-1].begin() as connection:
                    connection.execute(text(
                        "UPDATE analysis_jobs "
                        "SET lease_until=clock_timestamp()-interval '1 second' "
                        "WHERE id=:id"
                    ), {"id": job_id})
            assert await claim_job(factory) is None
            assert row(prepared, job_id) == ("FAILED", 3, None, None, "ANALYSIS_INTERRUPTED")

    asyncio.run(scenario())


@pytest.mark.parametrize(("status", "body"), [
    (403, {"detail": "forbidden"}),
    (422, {"detail": [{"type": "missing"}]}),
    (500, {"detail": "synthetic private upstream detail"}),
    (403, {"detail": {"code": "ANALYSIS_PROVIDER_UNAVAILABLE"}}),
])
def test_untyped_or_inconsistent_http_failure_cannot_requeue(queued_case, status, body):
    prepared, job_id = queued_case

    async def scenario():
        async with sessions() as factory:
            first = await claim_job(factory)
            assert first is not None
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json=body)
            )) as client:
                await process_job(factory, first, client,
                                  WorkerSettings("http://agent", INTERNAL_KEY))
            assert row(prepared, job_id) == ("FAILED", 1, None, None, "ANALYSIS_PROVIDER_INVALID")
            assert await claim_job(factory) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("withdrawal", ["actor", "source"])
def test_retried_execution_rechecks_actor_and_current_sources(queued_case, withdrawal):
    prepared, job_id = queued_case

    async def scenario():
        async with sessions() as factory:
            first = await claim_job(factory)
            assert first is not None
            await finish_job(factory, first, "ANALYSIS_PROVIDER_UNAVAILABLE")
            with prepared[-1].begin() as connection:
                if withdrawal == "actor":
                    connection.execute(text(
                        "UPDATE clinic_users SET status='REVOKED' WHERE id=:id"
                    ), {"id": first.actor_membership_id})
                else:
                    connection.execute(text("UPDATE users SET system_role=NULL "
                                            "WHERE system_role='LEGAL_EDITOR'"))
            second = await claim_job(factory)
            assert second is not None and second.id == first.id

            async def orchestrator(request):
                assert withdrawal == "source", "revoked actor must not call orchestrator"
                rejected = await asyncio.to_thread(submit, prepared, second)
                assert rejected.status_code == 409, rejected.text
                return httpx.Response(409, json={"detail": {
                    "code": rejected.json()["error"]["code"],
                }})

            async with httpx.AsyncClient(transport=httpx.MockTransport(orchestrator)) as client:
                await process_job(factory, second, client,
                                  WorkerSettings("http://agent", INTERNAL_KEY))
            assert row(prepared, job_id) == ("FAILED", 2, None, None,
                "ACTOR_NOT_AUTHORIZED" if withdrawal == "actor" else "LEGAL_EVIDENCE_UNAVAILABLE")
            assert await claim_job(factory) is None

    asyncio.run(scenario())


def test_retained_human_review_keeps_optional_draft_failure_without_normal_retry(routed_case):
    _, _, _, _, case, job, _, _, _, owner, _ = routed_case

    async def scenario():
        async with sessions() as factory:
            await finish_job(factory, job, "ANALYSIS_PROVIDER_UNAVAILABLE")
            assert await claim_job(factory) is None

    asyncio.run(scenario())
    with owner.connect() as connection:
        retained = connection.execute(text(
            "SELECT state,attempts,lease_token,lease_until,error_code "
            "FROM analysis_jobs WHERE id=:id"
        ), {"id": job.id}).one()
        assert retained == ("SUCCEEDED", 1, None, None, "ANALYSIS_PROVIDER_UNAVAILABLE")
        assert connection.scalar(text("SELECT count(*) FROM case_escalations WHERE case_id=:case"),
                                 {"case": case}) == 1
        assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:case"),
                                 {"case": case}) == 0
