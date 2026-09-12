import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client, seed_admin


pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


@pytest.fixture(autouse=True)
def worker_config(monkeypatch):
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "synthetic-internal-key-" + "x" * 32)


def seed_confirmed_case(actor_id: int) -> str:
    clinic_id, membership_id = seed_admin(actor_id)
    case_id = uuid4()
    engine = create_engine(owner_database_url())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO cases (id,clinic_id,created_by_membership_id,status,"
                "intake_schema_version,closed_at,retention_due_at) VALUES "
                "(:id,:clinic,:actor,'ANALYSIS_BLOCKED','dental-case-intake.v1',now(),"
                "now()+interval '30 days')"
            ),
            {"id": case_id, "clinic": clinic_id, "actor": membership_id},
        )
    engine.dispose()
    return str(case_id)


def test_analysis_enqueue_is_durable_deduplicated_and_tenant_scoped(monkeypatch):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "synthetic-internal-key-" + "x" * 32)
    case_id = seed_confirmed_case(9300130001)
    seed_admin(9300130002)
    with application_client() as client:
        first = client.post(
            f"/v1/cases/{case_id}/analysis-jobs",
            headers=actor_headers(9300130001),
            json={"messageId": 11},
        )
        assert first.status_code == 202, first.text
        job_id = first.json()["jobId"]
        second = client.post(
            f"/v1/cases/{case_id}/analysis-jobs",
            headers=actor_headers(9300130001),
            json={"messageId": 12},
        )
        assert second.json()["jobId"] == job_id
        assert (
            client.get(f"/v1/analysis-jobs/{job_id}", headers=actor_headers(9300130002)).status_code
            == 404
        )
    # A new app/process can read the acknowledged job, with no gateway memory.
    with application_client() as client:
        recovered = client.get(f"/v1/analysis-jobs/{job_id}", headers=actor_headers(9300130001))
        assert recovered.json()["state"] == "QUEUED"


def test_notification_discovery_requires_internal_key(monkeypatch):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "synthetic-internal-key-" + "x" * 32)
    with application_client() as client:
        assert client.get("/v1/internal/analysis-job-notifications").status_code == 403
        assert (
            client.get(
                "/v1/internal/analysis-job-notifications", headers={"X-Agent-Internal-Key": "wrong"}
            ).status_code
            == 403
        )


def test_job_claim_excludes_second_worker_and_fences_expired_lease():
    from legal_core.analysis_jobs import claim_job, require_job_lease
    from legal_core.case_api import ApiError, resolve_actor
    from legal_core.database import create_engine as async_engine
    from legal_core.database import create_session_factory

    case_id = seed_confirmed_case(9300130003)
    with application_client() as client:
        response = client.post(
            f"/v1/cases/{case_id}/analysis-jobs",
            headers=actor_headers(9300130003),
            json={"messageId": 21},
        )
        assert response.status_code == 202
        job_id = response.json()["jobId"]

    async def scenario():
        engine = async_engine()
        sessions = create_session_factory(engine)
        # A prior test may have queued another job; claim all available jobs, then find ours.
        claimed = []
        while job := await claim_job(sessions):
            claimed.append(job)
        ours = next(job for job in claimed if str(job.id) == job_id)
        assert await claim_job(sessions) is None
        async with sessions() as session:
            actor = await resolve_actor(session, 9300130003)
            await require_job_lease(session, actor, ours.case_id, ours.id, ours.lease_token)
            with pytest.raises(ApiError, match="lease"):
                await require_job_lease(session, actor, ours.case_id, ours.id, uuid4())
        owner = create_engine(owner_database_url())
        with owner.begin() as conn:
            conn.execute(
                text("UPDATE analysis_jobs SET lease_until=now()-interval '1 second' WHERE id=:id"),
                {"id": ours.id},
            )
        owner.dispose()
        replacement = await claim_job(sessions)
        assert replacement is not None and replacement.id == ours.id
        assert replacement.lease_token != ours.lease_token
        async with sessions() as session:
            actor = await resolve_actor(session, 9300130003)
            with pytest.raises(ApiError, match="lease"):
                await require_job_lease(session, actor, ours.case_id, ours.id, ours.lease_token)
        await engine.dispose()

    asyncio.run(scenario())


def test_committed_analysis_recovers_without_another_provider_call_and_notifies_once():
    from legal_core.analysis_jobs import claim_job
    from legal_core.analysis_job_worker import WorkerSettings, process_job
    from legal_core.database import create_engine as async_engine, create_session_factory
    from legal_core.models import IdempotencyRecord
    from test_analysis_api import _report_response
    import httpx

    case_id = seed_confirmed_case(9300130004)
    with application_client() as client:
        response = client.post(
            f"/v1/cases/{case_id}/analysis-jobs",
            headers=actor_headers(9300130004),
            json={"messageId": 31},
        )
        assert response.status_code == 202
        job_id = response.json()["jobId"]

    async def scenario():
        engine = async_engine()
        sessions = create_session_factory(engine)
        claimed = await claim_job(sessions)
        assert claimed is not None and str(claimed.id) == job_id
        report = _report_response().model_dump(mode="json", by_alias=True)
        report["caseId"] = case_id
        result = {
            "analysisAllowed": True,
            "riskLevel": "LOW",
            "escalationRequired": False,
            "report": report,
        }
        async with sessions() as session:
            await session.execute(
                text("SELECT set_config('app.current_clinic_id',:c,true)"),
                {"c": str(claimed.clinic_id)},
            )
            session.add(
                IdempotencyRecord(
                    clinic_id=claimed.clinic_id,
                    actor_membership_id=claimed.actor_membership_id,
                    scope=f"cases:{case_id}:analysis-submissions",
                    key=str(claimed.id),
                    request_sha256="a" * 64,
                    state="SUCCEEDED",
                    response_json=result,
                )
            )
            await session.commit()

        def fail_if_called(request):
            pytest.fail("a committed analysis must not invoke the provider again")

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail_if_called)) as http:
            await process_job(sessions, claimed, http, WorkerSettings("http://test", "x" * 32))
        await engine.dispose()

    asyncio.run(scenario())
    key = {"X-Agent-Internal-Key": "synthetic-internal-key-" + "x" * 32}
    with application_client() as client:
        status = client.get(f"/v1/analysis-jobs/{job_id}", headers=actor_headers(9300130004))
        assert status.json()["state"] == "SUCCEEDED"
        assert status.json()["result"]["riskLevel"] == "LOW"
        pending = client.get("/v1/internal/analysis-job-notifications", headers=key).json()["items"]
        assert any(item["jobId"] == job_id and item["messageId"] == 31 for item in pending)
        for _ in range(2):
            assert (
                client.post(
                    f"/v1/analysis-jobs/{job_id}/notification-ack",
                    headers=actor_headers(9300130004),
                ).status_code
                == 204
            )
        pending = client.get("/v1/internal/analysis-job-notifications", headers=key).json()["items"]
        assert all(item["jobId"] != job_id for item in pending)


def test_stale_worker_submission_rejected_before_evidence_or_model_output_is_used():
    from test_analysis_api import _submission

    case_id = seed_confirmed_case(9300130005)
    job_id = uuid4()
    headers = {
        **actor_headers(9300130005, job_id),
        "X-Analysis-Job-Id": str(job_id),
        "X-Analysis-Job-Token": str(uuid4()),
    }
    with application_client() as client:
        response = client.post(
            f"/v1/cases/{case_id}/analysis-submissions",
            headers=headers,
            json=_submission().model_dump(mode="json", by_alias=True),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ANALYSIS_JOB_LEASE_EXPIRED"


def test_enqueue_rejects_expired_content_before_replaying_a_job_or_creating_one():
    case_id = seed_confirmed_case(9300130006)
    empty_case_id = seed_confirmed_case(9300130007)
    with application_client() as client:
        response = client.post(
            f"/v1/cases/{case_id}/analysis-jobs",
            headers=actor_headers(9300130006),
            json={"messageId": 41},
        )
        assert response.status_code == 202
        job_id = response.json()["jobId"]
        owner = create_engine(owner_database_url())
        with owner.begin() as connection:
            connection.execute(
                text(
                    "UPDATE cases SET retention_due_at=now()-interval '1 second' "
                    "WHERE id IN (:one,:two)"
                ),
                {"one": case_id, "two": empty_case_id},
            )
        owner.dispose()
        for target, actor in [(case_id, 9300130006), (empty_case_id, 9300130007)]:
            response = client.post(
                f"/v1/cases/{target}/analysis-jobs",
                headers=actor_headers(actor),
                json={"messageId": 42},
            )
            assert response.status_code == 410
        assert (
            client.get(f"/v1/analysis-jobs/{job_id}", headers=actor_headers(9300130006)).status_code
            == 410
        )


def test_job_references_do_not_prevent_final_case_retention_purge():
    from legal_core.case_retention import purge_expired_case_content
    from legal_core.database import create_engine as async_engine, create_session_factory

    case_id = seed_confirmed_case(9300130008)
    with application_client() as client:
        response = client.post(
            f"/v1/cases/{case_id}/analysis-jobs",
            headers=actor_headers(9300130008),
            json={"messageId": 51},
        )
        assert response.status_code == 202
        job_id = response.json()["jobId"]
    owner = create_engine(owner_database_url())
    with owner.begin() as conn:
        conn.execute(
            text("UPDATE cases SET content_purged_at=now()-interval '13 months' WHERE id=:id"),
            {"id": case_id},
        )

    async def purge():
        engine = async_engine()
        await purge_expired_case_content(create_session_factory(engine))
        await engine.dispose()

    asyncio.run(purge())
    with owner.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM analysis_jobs WHERE id=:id"), {"id": job_id}
            ).scalar_one()
            == 0
        )
    owner.dispose()
