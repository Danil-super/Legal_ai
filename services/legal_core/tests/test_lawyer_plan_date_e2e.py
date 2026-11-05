"""Real HTTP/PG boundaries for the urgent case that cannot select a legal revision."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
from agent_orchestrator.main import ServiceDependencies, ServiceSettings, create_app
from legal_core.analysis_job_worker import WorkerSettings, process_job
from legal_core.database import database_url
from test_case_api import actor_headers
from test_lawyer_draft_context import routed_case as routed_case
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("routed_case", [{"eventDate": {"date": None, "precision": "UNKNOWN"}}],
                         indirect=True)
def test_urgent_unknown_date_is_visible_and_creator_can_recover_without_rewriting_case(
    routed_case, monkeypatch,
):
    monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "1")
    client, actor, _clinic, membership, case, job, _, _, early, owner, *_ = routed_case
    with owner.begin() as db:
        db.execute(text("UPDATE clinic_users SET role='CLINIC_LAWYER' WHERE id=:id"),
                   {"id": membership})
    model_calls = []

    class Reasoner:
        async def reason(self, *_args, **_kwargs):
            model_calls.append(True)
            pytest.fail("an uncertain legal date must be clarified before model I/O")

    async def bridge(request):
        actual = client.request(request.method, request.url.path, headers=request.headers,
                                content=request.content)
        return httpx.Response(actual.status_code, content=actual.content,
                              headers={"content-type": "application/json"})

    async def run():
        engine = create_async_engine(database_url())
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(bridge)) as core_http:
                app = create_app(settings=ServiceSettings(
                    INTERNAL_KEY, "http://core.test", "http://researcher", "r", "high-r",
                    "http://reviewer", "v", "high-v",
                ), dependencies=ServiceDependencies(
                    LegalCoreClient(LegalCoreEndpoint("http://core.test", INTERNAL_KEY),
                                    client=core_http), Reasoner(),
                ))
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                    await process_job(async_sessionmaker(engine, expire_on_commit=False), job,
                                      http, WorkerSettings("http://agent", INTERNAL_KEY))
        finally:
            await engine.dispose()

    asyncio.run(run())
    status = client.get(f"/v1/analysis-jobs/{job.id}", headers=actor_headers(actor))
    assert status.status_code == 200, status.text
    wire = status.json()
    assert wire["state"] == "SUCCEEDED" and wire["result"] == early
    assert wire["lawyerDraftAvailable"] is False
    assert wire["lawyerDraftErrorCode"] == "ANALYSIS_DATE_UNCERTAIN"
    assert wire["errorCode"] is None and model_calls == []
    path = f"/v1/cases/{case}/clarification-drafts"
    preview = client.get(path + "/preview", headers=actor_headers(actor),
                         params={"operation_id": early["source"]["operationId"]})
    assert preview.status_code == 200, preview.text
    bound = preview.json()
    bound.pop("caseId")
    key = uuid4()
    draft = client.post(path, headers=actor_headers(actor, key), json=bound)
    assert draft.status_code == 201, draft.text
    assert draft.json()["wizardState"] == "EVENT_DATE"
    assert "eventDate" not in draft.json()["draftData"]
    assert draft.json()["draftData"]["eventSummary"]
    replay = client.post(path, headers=actor_headers(actor, key), json=bound)
    assert replay.status_code == 200 and replay.json() == draft.json()
    with owner.connect() as db:
        assert db.scalar(text("SELECT status FROM cases WHERE id=:case"),
                         {"case": case}) == "ESCALATION_REQUIRED"
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions "
                              "WHERE case_id=:case"),
                         {"case": case}) == 0
        assert db.scalar(text("SELECT error_code FROM analysis_jobs WHERE id=:id"),
                         {"id": job.id}) == "ANALYSIS_DATE_UNCERTAIN"
    # Another clinic lawyer cannot supplement the creator's intake via the new path.
    other = next(iter(routed_case[10]))
    assert client.get(path + "/preview", headers=actor_headers(other),
                      params={"operation_id": early["source"]["operationId"]}).status_code == 404
