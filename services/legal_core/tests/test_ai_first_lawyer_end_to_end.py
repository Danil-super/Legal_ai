"""Actual worker→Agent→Core→PG→assigned human; only external models are synthetic."""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
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
from legal_core.analysis_jobs import claim_job
from legal_core.database import database_url
from legal_core.main import create_app
from telegram_gateway.dialogue_submission import present_submission
from test_case_api import actor_headers
from test_lawyer_draft_context import routed_case as routed_case
from test_reference_analogy_context import SCENARIO, seed_reference
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
ACTION = "Зафиксируйте обращение и сохраните относящиеся к нему документы."


@pytest.mark.parametrize("fault", [None, "lost-submit", "unsupported", "researcher-outage",
                                   "reference-examples", "owner-confirmed-reference"])
def test_actual_worker_routing_draft_replay_and_human_approval(routed_case, monkeypatch, fault):
    monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "1")
    client, actor, _, _, case, job, *_ = routed_case
    reference = None
    if fault in {"reference-examples", "owner-confirmed-reference"}:
        monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
        if fault == "owner-confirmed-reference":
            with routed_case[9].begin() as db:
                db.execute(text("UPDATE clinic_users SET role='CLINIC_OWNER' WHERE id=:id"),
                           {"id": routed_case[3]})
        reference = seed_reference(
            routed_case, owner_confirmation=fault == "owner-confirmed-reference")
    calls, lost, responses = [], [], []

    async def model(request):
        calls.append(request.url.host)
        wire = json.loads(request.content)
        context = json.loads(wire["messages"][1]["content"])
        if reference is not None:
            assert [item["scenarioText"] for item in context["referenceExamples"]] == [SCENARIO]
            assert context["referenceExamples"][0]["analogyId"] == "example-1"
            assert context["referenceExamples"][0]["purpose"] == "NON_AUTHORITATIVE_ANALOGY"
            assert reference not in json.dumps(context)
            assert "source" not in context["referenceExamples"][0]
        if request.url.host == "researcher.test":
            if fault == "researcher-outage":
                raise httpx.ReadTimeout("SYNTHETIC_MODEL_OUTAGE", request=request)
            body = {"claims": [{
                "claimId": "record", "kind": "ACTION", "text": ACTION,
                "evidenceFragmentIds": [context["evidence"][0]["fragmentId"]],
                "requiredFactKeys": ["EVENT_SUMMARY"],
            }], "internalRecommendations": [], "patientDraft": None, "clarificationRequests": []}
            if fault == "owner-confirmed-reference":
                body["claims"].append({
                    "claimId": "assessment", "kind": "LEGAL",
                    "text": "Обращение требует проверки по документам клиники.",
                    "evidenceFragmentIds": [context["evidence"][0]["fragmentId"]],
                    "requiredFactKeys": ["EVENT_SUMMARY"],
                })
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
                        if fault in {None, "lost-submit", "reference-examples",
                                     "owner-confirmed-reference"}:
                            before = len(calls)
                            # Same still-running lease after a crash/lost response.
                            await execute_job(sessions, job, worker_http, settings)
                            assert len(calls) == before == 2, responses
                        await finish_job(sessions, job, None)
        finally:
            await engine.dispose()

    # Drive the actual post-confirmation Gateway seam as well: an early escalation
    # must not return before dispatching the existing durable analysis intent.
    with routed_case[9].connect() as db:
        workflow_id = db.scalar(text("SELECT id FROM telegram_case_workflows WHERE case_id=:case"),
                                {"case": case})
    receipt = client.get(f"/v1/telegram-case-workflows/{workflow_id}",
                         headers=actor_headers(actor))
    assert receipt.status_code == 200 and receipt.json()["case"]["earlyEscalationId"]

    async def queue_and_process(_update, _context, case_id):
        queued = client.post(f"/v1/cases/{case_id}/analysis-jobs", headers=actor_headers(actor),
                             json={"messageId": 45})
        assert queued.status_code == 202 and queued.json()["jobId"] == str(job.id)
        await run()

    with (patch("telegram_gateway.dialogue_submission.enqueue_analysis", queue_and_process),
          patch("telegram_gateway.bot._reply", new_callable=AsyncMock)):
        asyncio.run(present_submission(
            SimpleNamespace(), SimpleNamespace(bot_data={"ai_first_lawyer_draft_enabled": True}),
            receipt.json(),
        ))
    available = fault in {None, "lost-submit", "reference-examples", "owner-confirmed-reference"}
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == int(available)
        assert db.scalar(text("SELECT count(*) FROM case_reports")) == 0
        assert db.scalar(text("SELECT status FROM cases WHERE id=:id"),
                         {"id": case}) == "ESCALATION_REQUIRED"
        assert db.scalar(text("SELECT state FROM analysis_jobs WHERE id=:id"),
                         {"id": job.id}) == "SUCCEEDED"
        notifications = db.scalar(text("SELECT count(*) FROM escalation_notifications"))
        assert notifications == len(routed_case[10])
        if reference is not None:
            assert db.scalar(text("SELECT reference_example_trace_sha256 "
                                  "FROM case_work_product_versions")) is not None
    if available:
        path = f"/v1/case-escalations/{routed_case[8]['escalationId']}"
        lawyer = next(iter(routed_case[10]))
        assert client.post(path + "/claim", headers=actor_headers(lawyer)).status_code == 200
        view = client.get(path + "/work-product", headers=actor_headers(lawyer))
        assert view.status_code == 200, view.text
        plan = view.json()
        assert plan["content"]["actions"] == [ACTION]
        if fault == "owner-confirmed-reference":
            assert plan["content"]["legalAssessment"] == [
                "Обращение требует проверки по документам клиники."]
        assert plan["citations"] and plan["reviewState"] == "UNAPPROVED"
        reviewed = client.post(path + "/work-product/reviews",
                                headers=actor_headers(lawyer, uuid4()), json={
            "expectedVersionId": plan["versionId"], "expectedContentSha256": plan["contentSha256"],
            "decision": "APPROVE",
        })
        assert reviewed.status_code == 201, reviewed.text
        if reference is not None:
            assert SCENARIO not in view.text and "referenceExamples" not in view.text
            assert client.get(f"/v1/reference-evaluations/{reference}",
                              headers=actor_headers(lawyer)).status_code == 403
            with routed_case[9].connect() as db:
                traces = db.scalars(text("SELECT reference_example_trace_sha256 "
                                         "FROM case_work_product_versions")).all()
                assert len(traces) == 2 and len(set(traces)) == 1 and traces[0] is not None
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
        if fault == "researcher-outage":
            path = f"/v1/case-escalations/{routed_case[8]['escalationId']}"
            assert client.get(path, headers=actor_headers(actor)).json()["canRequestAiPlan"]
            retry = client.post(f"/v1/cases/{case}/analysis-jobs", headers=actor_headers(actor),
                                json={"messageId": 99})
            assert retry.status_code == 202 and retry.json()["state"] == "QUEUED"

            async def reclaim():
                engine = create_async_engine(database_url())
                try:
                    return await claim_job(async_sessionmaker(engine, expire_on_commit=False))
                finally:
                    await engine.dispose()

            previous = job
            job = asyncio.run(reclaim())
            assert job.id == previous.id and job.lease_token != previous.lease_token
            fault = None  # The external provider has recovered.
            calls.clear()
            asyncio.run(run())
            lawyer = next(iter(routed_case[10]))
            assert client.post(path + "/claim", headers=actor_headers(lawyer)).status_code == 200
            plan = client.get(path + "/work-product", headers=actor_headers(lawyer))
            assert plan.status_code == 200 and plan.json()["content"]["actions"] == [ACTION]
            assert plan.json()["citations"] and plan.json()["reviewState"] == "UNAPPROVED"
            assert not client.get(path, headers=actor_headers(actor)).json()["canRequestAiPlan"]
