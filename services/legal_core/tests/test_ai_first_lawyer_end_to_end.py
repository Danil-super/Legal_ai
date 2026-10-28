"""Actual worker→Agent→Core→PG→assigned human; only external models are synthetic."""

import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
from agent_orchestrator.main import ServiceDependencies, ServiceSettings
from agent_orchestrator.main import create_app as create_agent
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.analysis_job_worker import WorkerSettings, execute_job, finish_job
from legal_core.database import database_url
from legal_core.main import create_app
from test_case_api import actor_headers
from test_lawyer_draft_context import routed_case as routed_case
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
ACTION = "Зафиксируйте обращение и сохраните относящиеся к нему документы."


@pytest.mark.parametrize("fault", [None, "lost-submit", "unsupported", "researcher-outage"])
def test_actual_worker_routing_draft_replay_and_human_approval(routed_case, monkeypatch, fault):
    monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "1")
    client, actor, _, _, case, job, *_ = routed_case
    calls, lost, responses = [], [], []

    async def model(request):
        calls.append(request.url.host)
        wire = json.loads(request.content)
        context = json.loads(wire["messages"][1]["content"])
        if request.url.host == "researcher.test":
            if fault == "researcher-outage":
                raise httpx.ReadTimeout("SYNTHETIC_MODEL_OUTAGE", request=request)
            body = {"claims": [{
                "claimId": "record", "kind": "ACTION", "text": ACTION,
                "evidenceFragmentIds": [context["evidence"][0]["fragmentId"]],
                "requiredFactKeys": ["EVENT_SUMMARY"],
            }], "internalRecommendations": [], "patientDraft": None, "clarificationRequests": []}
        else:
            body = {"reviews": [{
                "claimId": item["claimId"],
                "verdict": "UNSUPPORTED" if fault == "unsupported" else "SUPPORTED",
                "reviewedFragmentIds": item["evidenceFragmentIds"],
            } for item in context["claims"]]}
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps(body)}, "finish_reason": "stop",
        }]})

    async def run():
        engine = create_async_engine(database_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        core = create_app(session_factory=sessions, managed_engine=engine,
                          enable_draft_retention=False, enable_analysis_worker=False)
        try:
            async with (
                httpx.AsyncClient(transport=httpx.ASGITransport(app=core),
                                  base_url="http://core.test") as core_http,
                httpx.AsyncClient(transport=httpx.MockTransport(model)) as models,
            ):
                async def bridge(request):
                    response = await core_http.request(request.method, request.url.path,
                                                       headers=request.headers,
                                                       content=request.content)
                    responses.append((request.url.path, response.status_code, response.json()))
                    if (fault == "lost-submit" and request.url.path.endswith("lawyer-work-products")
                            and response.status_code == 201 and not lost):
                        lost.append(True)
                        raise httpx.ReadTimeout("SYNTHETIC_LOST_REPLY", request=request)
                    return httpx.Response(response.status_code, content=response.content,
                                          headers={"content-type": "application/json"})

                async with httpx.AsyncClient(transport=httpx.MockTransport(bridge)) as core_bridge:
                    agent = create_agent(
                        settings=ServiceSettings(INTERNAL_KEY, "http://core.test",
                            "http://researcher.test", "synthetic", "existing-high-r",
                            "http://reviewer.test", "synthetic", "existing-high-v"),
                        dependencies=ServiceDependencies(
                            LegalCoreClient(LegalCoreEndpoint("http://core.test", INTERNAL_KEY),
                                            client=core_bridge),
                            LegalReasoningOrchestrator(
                                researcher=HermesClient(HermesEndpoint("http://researcher.test",
                                    "synthetic", "existing-high-r"), client=models),
                                reviewer=HermesClient(HermesEndpoint("http://reviewer.test",
                                    "synthetic", "existing-high-v"), client=models),
                            ),
                        ),
                    )
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent),
                                                  base_url="http://agent.test") as worker_http:
                        settings = WorkerSettings("http://agent.test", INTERNAL_KEY)
                        await execute_job(sessions, job, worker_http, settings)
                        if fault in {None, "lost-submit"}:
                            before = len(calls)
                            # Same still-running lease after a crash/lost response.
                            await execute_job(sessions, job, worker_http, settings)
                            assert len(calls) == before == 2, responses
                        await finish_job(sessions, job, None)
        finally:
            await engine.dispose()

    asyncio.run(run())
    available = fault in {None, "lost-submit"}
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == int(available)
        assert db.scalar(text("SELECT count(*) FROM case_reports")) == 0
        assert db.scalar(text("SELECT status FROM cases WHERE id=:id"),
                         {"id": case}) == "ESCALATION_REQUIRED"
        assert db.scalar(text("SELECT state FROM analysis_jobs WHERE id=:id"),
                         {"id": job.id}) == "SUCCEEDED"
        notifications = db.scalar(text("SELECT count(*) FROM escalation_notifications"))
        assert notifications == len(routed_case[10])
    if available:
        path = f"/v1/case-escalations/{routed_case[8]['escalationId']}"
        lawyer = next(iter(routed_case[10]))
        assert client.post(path + "/claim", headers=actor_headers(lawyer)).status_code == 200
        view = client.get(path + "/work-product", headers=actor_headers(lawyer))
        assert view.status_code == 200, view.text
        plan = view.json()
        assert plan["content"]["actions"] == [ACTION]
        assert plan["citations"] and plan["reviewState"] == "UNAPPROVED"
        reviewed = client.post(path + "/work-product/reviews",
                                headers=actor_headers(lawyer, uuid4()), json={
            "expectedVersionId": plan["versionId"], "expectedContentSha256": plan["contentSha256"],
            "decision": "APPROVE",
        })
        assert reviewed.status_code == 201, reviewed.text
        delivered = client.get(path + "/work-product", headers=actor_headers(actor))
        assert delivered.status_code == 200 and delivered.json()["reviewState"] == "HUMAN_APPROVED"
        resolved = client.post(path + "/resolve", headers=actor_headers(lawyer),
                               json={"body": "Синтетическая проверка плана завершена."})
        assert resolved.status_code == 200, resolved.text
        retained = client.get(path + "/work-product", headers=actor_headers(actor))
        assert retained.status_code == 200 and retained.json()["reviewState"] == "HUMAN_APPROVED"
        assert client.post(path + "/work-product/reviews",
                           headers=actor_headers(lawyer, uuid4()), json={
            "expectedVersionId": reviewed.json()["versionId"],
            "expectedContentSha256": reviewed.json()["contentSha256"], "decision": "APPROVE",
        }).status_code == 403
    else:
        assert calls == (["researcher.test"] if fault == "researcher-outage"
                         else ["researcher.test", "reviewer.test"])
