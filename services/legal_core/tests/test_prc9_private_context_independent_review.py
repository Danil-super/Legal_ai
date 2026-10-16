"""Independent synthetic-only PostgreSQL probes for the private source boundary."""

import os
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from legal_core.case_api import ApiError
from legal_core.database import owner_database_url
from test_case_api import application_client, seed_admin
from test_intake_preparation_context import _binding, _context
from test_intake_preparation_lifecycle import SOURCE, _reserve, _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]


def _wait_for_operation_lock(engine):
    deadline = time.monotonic() + 5
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
        while time.monotonic() < deadline:
            waiting = observer.scalar(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND usename=:runtime AND wait_event_type='Lock' "
                "AND query LIKE '%idempotency_records%'"
            ), {"runtime": os.environ["POSTGRES_APP_USER"]})
            if waiting:
                return
            time.sleep(0.02)
    raise AssertionError("The private reader did not reach a real operation-row lock wait")


@pytest.mark.parametrize("fence,code", [
    ("lease", "PREPARATION_LEASE_EXPIRED"),
    ("retention", "INTAKE_DRAFT_EXPIRED"),
    ("entitlement", "SUBSCRIPTION_INACTIVE"),
    ("membership", "ACTOR_NOT_AUTHORIZED"),
    ("safe_stop", "ANALYSIS_SAFE_STOP"),
])
def test_real_operation_lock_wait_rechecks_current_fences(draft_owner, monkeypatch, fence, code):
    actor, clinic, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    intent = _binding(lease)
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            if fence == "lease":
                connection.execute(text(
                    "UPDATE idempotency_records SET lease_until=clock_timestamp() "
                    "+interval '2 seconds' WHERE id=:id"
                ), {"id": lease.operation_id})
            if fence == "retention":
                connection.execute(text(
                    "UPDATE telegram_intake_drafts SET purge_after=clock_timestamp() "
                    "+interval '2 seconds' WHERE id=:id"
                ), {"id": draft["id"]})
            if fence == "entitlement":
                connection.execute(text(
                    "UPDATE subscription_entitlements SET ends_at=clock_timestamp() "
                    "+interval '2 seconds' WHERE clinic_id=:id"
                ), {"id": clinic})
        with ThreadPoolExecutor(max_workers=1) as pool:
            with engine.begin() as blocker:
                blocker.execute(text(
                    "SELECT id FROM idempotency_records WHERE id=:id FOR UPDATE"
                ), {"id": lease.operation_id})
                future = pool.submit(_run, lambda sessions: _context(
                    sessions, actor, lease, intent,
                ))
                _wait_for_operation_lock(engine)
                if fence in {"lease", "retention", "entitlement"}:
                    blocker.execute(text("SELECT pg_sleep(2.1)"))
                elif fence == "membership":
                    blocker.execute(text(
                        "UPDATE clinic_users SET status='REVOKED' WHERE clinic_id=:id"
                    ), {"id": clinic})
                else:
                    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
            with pytest.raises(ApiError) as error:
                future.result(timeout=5)
        assert error.value.code == code
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                "AND action='INTAKE_PREPARATION_CONTEXT_READ'"
            ), {"id": lease.operation_id}) == 0
    finally:
        engine.dispose()


def test_same_clinic_owner_cannot_read_another_creators_source(draft_owner):
    actor, clinic, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    other = 20_800_000_000 + uuid4().int % 100_000_000
    other_clinic, other_member = seed_admin(other)
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM subscription_entitlements WHERE clinic_id=:id"
            ), {"id": other_clinic})
            connection.execute(text(
                "UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id"
            ), {"clinic": clinic, "id": other_member})
            connection.execute(text(
                "INSERT INTO subscription_entitlements "
                "(clinic_id,user_id,status,plan_code,starts_at) "
                "SELECT clinic_id,user_id,'ACTIVE','MVP',now()-interval '1 day' "
                "FROM clinic_users WHERE id=:id"
            ), {"id": other_member})
        with pytest.raises(ApiError) as error:
            _run(lambda sessions: _context(sessions, other, lease, _binding(lease)))
        assert error.value.status_code == 404
    finally:
        engine.dispose()


def test_context_audit_retains_only_revision_not_source_or_lease(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    _run(lambda sessions: _context(sessions, actor, lease, _binding(lease)))
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            retained = connection.scalar(text(
                "SELECT row_to_json(a)::text FROM audit_events a WHERE resource_id=:id "
                "AND action='INTAKE_PREPARATION_CONTEXT_READ'"
            ), {"id": lease.operation_id})
            assert retained is not None
            assert SOURCE not in retained
            assert str(lease.lease_token) not in retained
            metadata = connection.scalar(text(
                "SELECT metadata_json FROM audit_events WHERE resource_id=:id "
                "AND action='INTAKE_PREPARATION_CONTEXT_READ'"
            ), {"id": lease.operation_id})
            assert metadata == {"revision": draft["revision"]}
    finally:
        engine.dispose()


@pytest.mark.parametrize("change,status,model_calls", [
    ({}, 200, 1), ({"leaseToken": str(uuid4())}, 409, 0),
    ({"draftId": str(uuid4())}, 503, 0),
    ({"source": "synthetic arbitrary caller source"}, 422, 0),
])
def test_agent_core_postgres_roundtrip_minimizes_model_input_and_releases_locks(
    draft_owner, monkeypatch, change, status, model_calls,
):
    from agent_orchestrator.intake_understanding import IntakeUnderstanding
    from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
    from agent_orchestrator.main import ServiceDependencies, ServiceSettings, create_app

    key = "independent-synthetic-private-key-1234567890"
    monkeypatch.setenv("AGENT_INTERNAL_KEY", key)
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    intent = _binding(lease)
    calls = []
    engine = create_engine(owner_database_url())

    async def complete_json(**kwargs):
        calls.append(kwargs)
        assert json.loads(kwargs["user"]) == {"source": SOURCE}
        for private in (str(lease.operation_id), draft["id"], str(lease.lease_token),
                        intent["inputSha256"]):
            assert private not in json.dumps(kwargs)
        with engine.begin() as connection:
            connection.execute(text(
                "SELECT id FROM telegram_intake_drafts WHERE id=:id FOR UPDATE NOWAIT"
            ), {"id": draft["id"]})
            assert connection.scalar(text(
                "SELECT state FROM idempotency_records WHERE id=:id FOR UPDATE NOWAIT"
            ), {"id": lease.operation_id}) == "IN_PROGRESS"
        return {"clinicActions": ["INVITED_FOR_EXAMINATION"],
                "sourceSpans": {"clinicActions": [{"start": 0, "end": 26}]}}

    try:
        with application_client() as core_http:
            async def transport(request):
                response = core_http.request(
                    request.method, request.url.path,
                    headers=dict(request.headers), content=request.content,
                )
                return httpx.Response(response.status_code, content=response.content,
                                      headers={"content-type": "application/json"})

            async def invoke():
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
                    core = LegalCoreClient(LegalCoreEndpoint("http://core:8000", key), client=http)
                    settings = ServiceSettings(key, "http://core:8000", "http://researcher:8642",
                                               "r", "researcher", "http://reviewer:8642",
                                               "v", "reviewer")
                    app = create_app(settings=settings, dependencies=ServiceDependencies(
                        legal_core=core, reasoning=SimpleNamespace(),
                        understanding=IntakeUnderstanding(researcher=SimpleNamespace(
                            complete_json=complete_json,
                        )),
                    ))
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url="http://agent:8000") as agent:
                        return await agent.post(
                            f"/v1/intake-preparations/{lease.operation_id}/understand",
                            headers={"X-Agent-Internal-Key": key,
                                     "X-Telegram-User-Id": str(actor)},
                            json={"draftId": draft["id"], "leaseToken": str(lease.lease_token),
                                  "expectedRevision": draft["revision"],
                                  "expectedInputSha256": intent["inputSha256"], **change},
                        )
            response = asyncio.run(invoke())
        assert response.status_code == status, response.text
        assert len(calls) == model_calls
        assert SOURCE not in response.text and str(lease.lease_token) not in response.text
        with engine.connect() as connection:
            stored = connection.scalar(text(
                "SELECT response_json FROM idempotency_records WHERE id=:id"
            ), {"id": lease.operation_id})
            assert stored["suggestions"] is None
            assert connection.scalar(text(
                "SELECT revision FROM telegram_intake_drafts WHERE id=:id"
            ), {"id": draft["id"]}) == draft["revision"]
    finally:
        engine.dispose()
