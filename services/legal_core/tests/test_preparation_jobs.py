"""Versioned result readers and worker recovery, without enabling V2 producers."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from legal_core.analysis_jobs import (
    AnalysisJob,
    ClaimedJob,
    PreparationJobResponse,
    job_result,
)
from legal_core.analysis_job_worker import WorkerSettings, process_job
from legal_core.analysis_contracts import PreparationResult
from legal_core.database import database_url, owner_database_url
from legal_core.models import Case, IdempotencyRecord
from test_case_api import actor_headers, application_client, seed_admin
from test_case_preparation_contracts import _human_review, _limited, _needs_facts, _ready


@pytest.mark.parametrize("make_result", [_needs_facts, _limited, _human_review, _ready])
def test_v2_success_includes_a_typed_intermediate_or_ready_result(make_result):
    result = make_result()
    parsed = PreparationJobResponse.model_validate({
        "resultVersion": 2, "jobId": str(uuid4()), "caseId": result["source"]["caseId"],
        "state": "SUCCEEDED", "result": result,
    })
    assert parsed.result.root.status == result["status"]
    assert PreparationResult.model_validate(
        parsed.model_dump(mode="json", by_alias=True)["result"],
    ) == PreparationResult.model_validate(result)


@pytest.mark.parametrize("make_result", [_needs_facts, _limited, _human_review, _ready])
def test_core_to_gateway_reader_offers_a_pdf_only_for_verified_ready(make_result):
    from telegram_gateway.analysis_jobs_runtime import JobSnapshot, job_message
    result = make_result()
    wire = PreparationJobResponse.model_validate({
        "resultVersion": 2, "jobId": str(uuid4()), "caseId": result["source"]["caseId"],
        "state": "SUCCEEDED", "result": result,
    }).model_dump(mode="json", by_alias=True)
    snapshot = JobSnapshot.parse(wire)
    text, keyboard = job_message(snapshot)
    pdf_buttons = [button for row in keyboard.inline_keyboard for button in row
                   if button.callback_data.startswith("analysis:pdf:")]
    assert bool(pdf_buttons) is (result["status"] == "READY")
    assert text


def test_succeeded_job_with_missing_cache_is_a_bounded_terminal_failure(stored_preparation_job):
    actor, job, _, record, _, _, engine = stored_preparation_job
    with engine.begin() as connection:
        connection.execute(text("UPDATE idempotency_records SET response_json=NULL WHERE id=:id"),
                           {"id": record})
        connection.execute(text("UPDATE analysis_jobs SET state='SUCCEEDED',lease_token=NULL,"
                                "lease_until=NULL WHERE id=:id"), {"id": job})
    with application_client() as client:
        response = client.get(f"/v1/analysis-jobs/{job}", headers=actor_headers(actor))
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "FAILED"
    assert response.json()["errorCode"] == "ANALYSIS_RESULT_MISSING"
    assert response.json()["result"] is None


@pytest.mark.parametrize("change", [
    {"resultVersion": 2.0}, {"resultVersion": True}, {"resultVersion": 3},
    {"state": "SUCCEEDED", "result": None}, {"state": "RUNNING"},
    {"state": "FAILED"}, {"caseId": str(uuid4())}, {"clinicId": str(uuid4())},
])
def test_v2_job_rejects_unknown_versions_incoherent_state_or_wrong_case(change):
    result = _needs_facts()
    payload = {
        "resultVersion": 2, "jobId": str(uuid4()), "caseId": result["source"]["caseId"],
        "state": "SUCCEEDED", "result": result,
    }
    with pytest.raises(ValidationError):
        PreparationJobResponse.model_validate(payload | change)


@pytest.fixture
def stored_preparation_job():
    if os.getenv("POSTGRES_INTEGRATION") != "1":
        pytest.skip("requires disposable PostgreSQL")
    actor_id = 18_400_000_000 + uuid4().int % 100_000_000
    clinic, membership = seed_admin(actor_id)
    engine = create_engine(owner_database_url())
    try:
        with Session(engine, expire_on_commit=False) as session, session.begin():
            case = Case(clinic_id=clinic, created_by_membership_id=membership,
                        status="ANALYSIS_BLOCKED", closed_at=datetime.now(UTC),
                        retention_due_at=datetime.now(UTC) + timedelta(days=30))
            session.add(case)
            session.flush()
            job = AnalysisJob(
                clinic_id=clinic, case_id=case.id, actor_membership_id=membership,
                message_id=456, result_version=2, state="RUNNING", lease_token=uuid4(),
                lease_until=datetime.now(UTC) + timedelta(minutes=3),
            )
            session.add(job)
            session.flush()
            result = _needs_facts()
            result["source"]["caseId"] = str(case.id)
            record = IdempotencyRecord(
                clinic_id=clinic, actor_membership_id=membership,
                scope=f"cases:{case.id}:preparation-submissions", key=str(job.id),
                request_sha256="a" * 64, state="SUCCEEDED", response_json=result,
                resource_type="CASE_PREPARATION", resource_id=case.id,
            )
            session.add(record)
            session.flush()
            claimed = ClaimedJob(job.id, clinic, case.id, membership, actor_id, job.lease_token)
        yield actor_id, job.id, case.id, record.id, claimed, result, engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE analysis_jobs SET notified_at=clock_timestamp(), "
                "state=CASE WHEN state='RUNNING' THEN 'FAILED' ELSE state END, "
                "lease_token=NULL,lease_until=NULL WHERE id=:id"
            ), {"id": job.id})
        engine.dispose()


def _run(operation):
    async def scenario():
        engine = create_async_engine(database_url())
        try:
            return await operation(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()
    return asyncio.run(scenario())


def test_intermediate_cache_finishes_success_without_another_model_call_and_acks(
    stored_preparation_job,
):
    actor, job, case, _, claimed, result, engine = stored_preparation_job

    async def scenario(sessions):
        def forbidden_request(request):
            pytest.fail("retained intermediate result must not repeat model analysis")
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden_request)) as client:
            await process_job(sessions, claimed, client, WorkerSettings("http://test", "x" * 32))
    _run(scenario)
    with application_client() as client:
        status = client.get(f"/v1/analysis-jobs/{job}", headers=actor_headers(actor))
        assert status.status_code == 200, status.text
        assert status.json()["resultVersion"] == 2
        assert status.json()["state"] == "SUCCEEDED"
        assert PreparationResult.model_validate(status.json()["result"]) == (
            PreparationResult.model_validate(result)
        )
        assert client.post(f"/v1/analysis-jobs/{job}/notification-ack",
                           headers=actor_headers(actor)).status_code == 204
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT notified_at IS NOT NULL FROM analysis_jobs WHERE id=:id"
        ), {"id": job})
        assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:id"),
                                 {"id": case}) == 0


def test_v2_job_does_not_replay_an_old_namespace_as_a_new_result(stored_preparation_job):
    actor, job_id, case, record, _, _, engine = stored_preparation_job
    with engine.begin() as connection:
        connection.execute(text("UPDATE idempotency_records SET scope=:scope WHERE id=:id"),
                           {"scope": f"cases:{case}:analysis-submissions", "id": record})

    async def scenario(sessions):
        async with sessions() as session:
            from legal_core.case_api import resolve_actor
            await resolve_actor(session, actor)
            job = await session.get(AnalysisJob, job_id)
            assert await job_result(session, job) is None
    _run(scenario)


@pytest.mark.parametrize("tamper", ["version", "source_case", "resource", "invalid_question"])
def test_bad_retained_results_fail_bounded_and_do_not_leave_worker_running(
    stored_preparation_job, tamper,
):
    actor, job, _, record, claimed, _, engine = stored_preparation_job
    assignments = {
        "version": "response_json=jsonb_set(response_json,'{resultVersion}','3')",
        "source_case": (
            "response_json=jsonb_set(response_json,'{source,caseId}',"
            "to_jsonb(CAST(:other AS text)))"
        ),
        "resource": "resource_type='INTAKE_PREPARATION'",
        "invalid_question": (
            "response_json=jsonb_set(response_json,'{clarificationRequests}',"
            "'[{\"questionId\":\"UNTRUSTED_SYNTHETIC_QUESTION\"}]')"
        ),
    }
    with engine.begin() as connection:
        connection.execute(
            text(f"UPDATE idempotency_records SET {assignments[tamper]} WHERE id=:id"),
            {"id": record, "other": str(uuid4())},
        )
    with application_client() as client:
        response = client.get(f"/v1/analysis-jobs/{job}", headers=actor_headers(actor))
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "ANALYSIS_RESULT_INVALID"
    assert "UNTRUSTED" not in response.text

    async def scenario(sessions):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid retained data must not call models"),
        )) as client:
            await process_job(sessions, claimed, client, WorkerSettings("http://test", "x" * 32))
    _run(scenario)
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT state,error_code,lease_token FROM analysis_jobs WHERE id=:id"
        ), {"id": job}).one() == ("FAILED", "ANALYSIS_RESULT_INVALID", None)
    with application_client() as client:
        failure = client.get(f"/v1/analysis-jobs/{job}", headers=actor_headers(actor))
        assert failure.status_code == 200, failure.text
        assert failure.json()["state"] == "FAILED"
        assert failure.json()["result"] is None
        assert failure.json()["errorCode"] == "ANALYSIS_RESULT_INVALID"
        assert client.post(f"/v1/analysis-jobs/{job}/notification-ack",
                           headers=actor_headers(actor)).status_code == 204


@pytest.mark.parametrize("reason", ["foreign", "revoked", "expired"])
def test_intermediate_job_status_is_not_a_retention_or_access_bypass(
    stored_preparation_job, reason,
):
    actor, job, case, _, _, _, engine = stored_preparation_job
    expected = 404
    if reason == "foreign":
        actor += 100_000_000
        seed_admin(actor)
    else:
        with engine.begin() as connection:
            if reason == "revoked":
                connection.execute(text(
                    "UPDATE clinic_users SET status='REVOKED' WHERE id="
                    "(SELECT actor_membership_id FROM analysis_jobs WHERE id=:id)"
                ), {"id": job})
                expected = 403
            else:
                connection.execute(text("UPDATE cases SET retention_due_at=clock_timestamp() "
                                        "-interval '1 second' WHERE id=:id"), {"id": case})
                expected = 410
    try:
        with application_client() as client:
            response = client.get(f"/v1/analysis-jobs/{job}", headers=actor_headers(actor))
        assert response.status_code == expected, response.text
        assert "RESPONSE_PURPOSE" not in response.text
    finally:
        if reason == "expired":
            with engine.begin() as connection:
                connection.execute(text("UPDATE cases SET retention_due_at=clock_timestamp() "
                                        "+interval '30 days' WHERE id=:id"), {"id": case})
