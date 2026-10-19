"""Durable workflow intent selects the queue format, never a client-supplied flag."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
from agent_orchestrator.main import ServiceDependencies, ServiceSettings, create_app
from legal_core.analysis_job_worker import WorkerSettings, worker_once
from legal_core.database import database_url
from test_case_api import actor_headers, seed_admin
from test_report_free_workflow import _payload
from test_report_free_workflow import isolated_database as isolated_database
from test_report_free_workflow import pg
from test_report_free_workflow import preparation_workflow as preparation_workflow
from test_verified_copy_analysis_e2e import INTERNAL_KEY

pytestmark = pg


def _confirmed(fixture, *, version=2, **changes):
    core, actor, *_ = fixture
    payload = _payload(**changes)
    if version == 1:
        payload.pop("receiptVersion")
    created = core.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                        headers=actor_headers(actor), json=payload)
    assert created.status_code == 201, created.text
    return created.json()["case"]["id"]


@pytest.mark.parametrize("event,reason", [
    ({"date": "2026-09-12", "precision": "EXACT"}, "LEGAL_EVIDENCE_UNAVAILABLE"),
    ({"date": None, "precision": "UNKNOWN"}, "ANALYSIS_DATE_UNCERTAIN"),
    ({"date": "2026-09-01", "precision": "APPROXIMATE"}, "ANALYSIS_DATE_UNCERTAIN"),
])
def test_initial_receipt_drives_real_worker_agent_core_without_preliminary_pdf(
    preparation_workflow, event, reason,
):
    core, actor, _, _, engine = preparation_workflow
    case = _confirmed(preparation_workflow, eventDate=event)
    headers = actor_headers(actor)
    first = core.post(f"/v1/cases/{case}/analysis-jobs", headers=headers, json={"messageId": 45})
    assert first.status_code == 202, first.text
    assert first.json()["resultVersion"] == 2
    repeated = core.post(f"/v1/cases/{case}/analysis-jobs", headers=headers,
                         json={"messageId": 46})
    assert repeated.json() == first.json()
    job = first.json()["jobId"]
    reasoning = SimpleNamespace(reason=AsyncMock(side_effect=AssertionError(
        "no model before context",
    )))
    requests = []

    def core_bridge(request):
        requests.append(request.url.path)
        actual = core.request(request.method, str(request.url), headers=dict(request.headers),
                              content=request.content)
        return httpx.Response(actual.status_code, content=actual.content,
                              headers={"Content-Type": "application/json"})

    async def run():
        worker_engine = create_async_engine(database_url())
        sessions = async_sessionmaker(worker_engine, expire_on_commit=False)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(core_bridge)) as core_http:
                agent = create_app(settings=ServiceSettings(
                    INTERNAL_KEY, "http://core.test",
                    "http://researcher", "synthetic", "researcher",
                    "http://reviewer", "synthetic", "reviewer",
                ), dependencies=ServiceDependencies(
                    legal_core=LegalCoreClient(LegalCoreEndpoint("http://core.test", INTERNAL_KEY),
                                               client=core_http), reasoning=reasoning,
                ))
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=agent),
                ) as worker_http:
                    assert await worker_once(sessions, worker_http,
                                             WorkerSettings("http://orchestrator", INTERNAL_KEY))
        finally:
            await worker_engine.dispose()

    asyncio.run(run())
    reasoning.reason.assert_not_awaited()
    assert requests == [f"/v1/cases/{case}/preparation-context"]
    result = core.get(f"/v1/analysis-jobs/{job}", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "SUCCEEDED"
    assert result.json()["resultVersion"] == 2
    assert result.json()["result"]["status"] == "LIMITED"
    assert result.json()["result"]["reasonCode"] == reason
    assert "report" not in result.json()["result"]
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM analysis_jobs")) == 1
        assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0
        assert connection.scalar(text("SELECT result_version FROM analysis_jobs")) == 2


def test_ordinary_legacy_workflow_still_creates_a_legacy_job(preparation_workflow):
    core, actor, _, _, engine = preparation_workflow
    case = _confirmed(preparation_workflow, version=1)
    response = core.post(f"/v1/cases/{case}/analysis-jobs", headers=actor_headers(actor),
                         json={"messageId": 45})
    assert response.status_code == 202, response.text
    assert "resultVersion" not in response.json()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT result_version FROM analysis_jobs")) == 1


def test_an_incompatible_existing_job_cannot_downgrade_the_workflow(preparation_workflow):
    core, actor, _, _, engine = preparation_workflow
    case = _confirmed(preparation_workflow)
    headers = actor_headers(actor)
    first = core.post(f"/v1/cases/{case}/analysis-jobs", headers=headers, json={"messageId": 45})
    assert first.status_code == 202, first.text
    with engine.begin() as connection:
        connection.execute(text("UPDATE analysis_jobs SET result_version=1 WHERE case_id=:id"),
                           {"id": case})
    denied = core.post(f"/v1/cases/{case}/analysis-jobs", headers=headers, json={"messageId": 46})
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "ANALYSIS_RESULT_VERSION_CONFLICT"
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM analysis_jobs")) == 1


def test_enqueue_version_cannot_be_selected_through_untrusted_json(preparation_workflow):
    core, actor, _, _, engine = preparation_workflow
    case = _confirmed(preparation_workflow)
    denied = core.post(f"/v1/cases/{case}/analysis-jobs", headers=actor_headers(actor),
                       json={"messageId": 45, "resultVersion": 2})
    assert denied.status_code == 422, denied.text
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM analysis_jobs")) == 0


@pytest.mark.parametrize("replay", [False, True])
def test_case_creator_is_rechecked_after_the_actual_case_lock_wait(preparation_workflow, replay):
    core, actor, clinic, _, engine = preparation_workflow
    case = _confirmed(preparation_workflow)
    _, other_membership = seed_admin(actor + 1_000_000_000, entitlement_status=None)
    with engine.begin() as connection:
        connection.execute(text("UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id"),
                           {"clinic": clinic, "id": other_membership})

    def enqueue():
        return core.post(f"/v1/cases/{case}/analysis-jobs", headers=actor_headers(actor),
                         json={"messageId": 45})

    if replay:
        assert enqueue().status_code == 202
    blocker = engine.connect()
    transaction = blocker.begin()
    blocker.execute(text("SELECT id FROM cases WHERE id=:id FOR UPDATE"), {"id": case})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(enqueue)
        try:
            waiting = False
            deadline = monotonic() + 10
            while monotonic() < deadline:
                with engine.connect() as observer:
                    waiting = bool(observer.scalar(text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND usename='dental_legal_app' AND wait_event_type='Lock' "
                        "AND query LIKE '%cases%'"
                    )))
                if waiting:
                    break
                Event().wait(0.02)
            assert waiting, "actual PostgreSQL case wait must occur before mutation"
            blocker.execute(text("UPDATE cases SET created_by_membership_id=:other WHERE id=:id"),
                            {"other": other_membership, "id": case})
        finally:
            transaction.commit()
            blocker.close()
        denied = future.result(15)
    assert denied.status_code == 404, denied.text
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM analysis_jobs")) == int(replay)
