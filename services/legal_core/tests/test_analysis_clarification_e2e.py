"""Fictional clarification through Telegram, HTTP services, PostgreSQL and MinIO.

Only Telegram delivery and external Hermes HTTP responses are synthetic. This proves
composition and evidence gates, not live-model quality or production legal readiness.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from telegram import Update

from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
from agent_orchestrator.main import ServiceDependencies, ServiceSettings
from agent_orchestrator.main import create_app as create_orchestrator
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.analysis_job_worker import WorkerSettings, worker_once
from legal_core.analysis_clarifications import ClarificationRequest
from legal_core.corpus_loader import ingest_manifest
from legal_core.database import database_url, owner_database_url
from legal_core.factual_safety_risk import SCREENING_SIGNALS
from legal_core.main import create_app
from legal_core.risk_policy_approval import RiskPolicyApproval, approve_risk_policy
from telegram_gateway import bot
from telegram_gateway.analysis_jobs_runtime import (
    AnalysisJobsClient, JOBS_CLIENT_KEY, JobSnapshot, job_message,
)
from telegram_gateway.case_wizard import LegalCoreClient as GatewayCoreClient
from test_case_api import actor_headers, seed_admin
from test_case_experience import harness
from test_clarification_recovery import _source_rows
from test_dialog_isolation import ACTOR
from test_legal_editor_workspace_api import _seed_user, _write_manifest
from test_risk_policy_v4 import v4_approval
from test_verified_copy_analysis_e2e import EDITOR_KEY, INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL and MinIO"
)
ACTION = "Зафиксировать обращение пациента во внутреннем журнале."
EVIDENCE = "Синтетическая учебная норма: платные медицинские услуги. " + ACTION
NARRATIVE = "Синтетический пациент сообщил о дискомфорте после лечения."
RAW_MATERIAL = b"Synthetic original material sentinel; never include in model input."
REQUESTS = [
    {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
    {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0},
    {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None},
]


def _run_status(case_id):
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            return connection.execute(text(
                "SELECT verifier_status, as_of_date FROM case_analysis_runs "
                "WHERE case_id=:id ORDER BY created_at DESC LIMIT 1"
            ), {"id": case_id}).one()
    finally:
        engine.dispose()


async def _clarification_intent(app, callback, update_id):
    # The shared harness infers message IDs from its delivery list. A persisted
    # button intent instead needs the same Telegram message ID on both retries.
    await app.process_update(Update.de_json({
        "update_id": update_id,
        "callback_query": {
            "id": str(update_id), "chat_instance": "synthetic",
            "data": callback,
            "from": {"id": ACTOR, "is_bot": False, "first_name": "Synthetic"},
            "message": {"message_id": 610, "date": 1,
                        "chat": {"id": ACTOR, "type": "private"}},
        },
    }, app.bot))


async def _approve_evidence(core_http, version_id, editor):
    headers = {
        **actor_headers(editor, uuid4()), "X-Legal-Editor-Gateway-Key": EDITOR_KEY,
    }
    detail = await core_http.get(f"/v1/legal/review-queue/{version_id}", headers=headers)
    assert detail.status_code == 200 and detail.json()["approvalEligible"], detail.text
    version = detail.json()
    approved = await core_http.post(
        f"/v1/legal/review-queue/{version_id}/approval-events", headers=headers,
        json={
            "expectedSha256": version["rawSha256"],
            "expectedNormalizedSha256": version["normalizedSha256"],
            "expectedFragmentsSha256": version["fragmentsSha256"],
            "expectedEffectiveFrom": version["effectiveFrom"],
            "expectedEffectiveTo": version["effectiveTo"],
            "sourceIsOfficial": True, "officialTextCompared": True,
            "artifactIsComplete": True, "effectiveDatesVerified": True, "fragmentsVerified": True,
        },
    )
    assert approved.status_code == 200, approved.text
    return version


async def _enter_intake(app, client, dispatch, entry, core_http):
    if entry == "quick":
        await dispatch("quick:start")
        await dispatch("text:" + NARRATIVE)
        pending = app.user_data[ACTOR]["quick_intake_candidate"]
        await dispatch(f"quick:review:{pending['nonce']}:accept")
        drafts = await client.list_intake_drafts(ACTOR)
        draft_id = drafts["items"][0]["id"]
    else:
        await dispatch("case:start")
        draft_id = app.user_data[ACTOR][bot.WIZARD_DATA_KEY]["draft_id"]
    uploaded = await core_http.post(
        f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
        headers={**actor_headers(ACTOR, uuid4()), "X-Source-Filename": "synthetic.txt",
                 "Content-Type": "text/plain"}, content=RAW_MATERIAL,
    )
    assert uploaded.status_code == 201, uploaded.text
    # Reload the real server revision after the material upload.
    await dispatch("/cancel")
    await dispatch(f"case:draft:{draft_id}")
    for callback in (
        "case:v2:single:incomingKind:COMPLAINT",
        "case:v2:multi:situationAreas:TREATMENT", "case:v2:multi:situationAreas:DONE",
    ):
        await dispatch(callback)
    await dispatch("text:Синтетическое терапевтическое лечение")
    if entry == "guided":
        await dispatch("text:" + NARRATIVE)
    await dispatch("text:12.09.2026")
    for callback in (
        "case:v2:single:conflictStage:FIRST",
        "case:v2:multi:clinicActions:INVITED_FOR_EXAMINATION", "case:v2:multi:clinicActions:DONE",
        "case:v2:multi:healthSignals:NO_KNOWN_INFORMATION", "case:v2:multi:healthSignals:DONE",
        "case:v2:material:skip",
    ):
        await dispatch(callback)
    for field in SCREENING_SIGNALS:
        await dispatch(f"case:safety:{field}:NO")
    await dispatch("case:v2:summary:confirm")
    await dispatch(f"case:confirm:{draft_id}")
    return await client.get_workflow(UUID(draft_id), ACTOR)


@pytest.mark.parametrize("entry,outcome", [
    ("quick", "positive"), ("guided", "positive"), ("quick", "unknown"),
    ("guided", "earlier-date"), ("guided", "unsupported"),
])
def test_factual_clarification_round_trip_preserves_history_and_core_evidence_gates(
    isolated_database, tmp_path: Path, entry: str, outcome: str,
):
    seed_admin(ACTOR, role="CLINIC_OWNER")
    outsider = ACTOR + 1
    seed_admin(outsider, role="CLINIC_OWNER")
    editor = 93_000_000_000 + uuid4().int % 100_000_000
    _seed_user(editor, system_role="LEGAL_EDITOR")
    manifest = _write_manifest(
        tmp_path, fragment_text=EVIDENCE, effective_from="2026-08-04", effective_to="2027-08-04",
    )
    projections = []
    model_calls = []
    clarification_posts = []

    async def observe_request(request):
        if request.method == "POST" and request.url.path.endswith("/clarification-drafts"):
            clarification_posts.append((request.headers.get("Idempotency-Key"),
                                        json.loads(request.content)))

    def model_http(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        projection = json.loads(body["messages"][-1]["content"])
        projections.append(projection)
        model_calls.append(body["model"])
        assert RAW_MATERIAL.decode() not in request.content.decode()
        assert "synthetic.txt" not in request.content.decode()
        assert projection["asOfDate"] == "2026-09-12"
        assert len(projection["evidence"]) == 1
        evidence = projection["evidence"][0]
        assert evidence["text"] == EVIDENCE
        assert evidence["effectiveFrom"] == "2026-08-04"
        fragment_id = evidence["fragmentId"]
        if body["model"] == "researcher":
            answers = projection["facts"].get("ANALYSIS_CLARIFICATION_ANSWERS")
            result = {
                "claims": [], "internalRecommendations": ["UNVERIFIED SENTINEL"],
                "patientDraft": "UNVERIFIED SENTINEL", "clarificationRequests": [],
            }
            if answers is None:
                result["clarificationRequests"] = REQUESTS
            elif outcome != "unknown":
                result["claims"] = [{
                    "claimId": "record-appeal", "kind": "ACTION", "text": ACTION,
                    "evidenceFragmentIds": [fragment_id],
                    "requiredFactKeys": ["ANALYSIS_CLARIFICATION_ANSWERS", "AFFECTED_SERVICES"],
                }]
                result["internalRecommendations"] = []
                result["patientDraft"] = None
            else:
                assert answers["answers"][0]["value"] == "UNKNOWN"
        else:
            assert body["model"] == "reviewer"
            result = {"reviews": [{
                "claimId": "record-appeal",
                "verdict": "UNSUPPORTED" if outcome == "unsupported" else "SUPPORTED",
                "reviewedFragmentIds": [fragment_id],
            }]}
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(result, ensure_ascii=False),
        }}]})

    async def scenario():
        engine = create_async_engine(database_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        version_id = await ingest_manifest(sessions, manifest)
        await approve_risk_policy(sessions, RiskPolicyApproval(
            reviewer_telegram_user_id=editor, high_demand_threshold_kopecks=5_000_000,
            incident_triggers_reviewed=True, monetary_threshold_reviewed=True,
            escalation_rules_reviewed=True,
        ))
        await approve_risk_policy(sessions, v4_approval(reviewer_telegram_user_id=editor))
        core = create_app(
            session_factory=sessions, managed_engine=engine,
            enable_draft_retention=False, enable_analysis_worker=False,
        )
        async with (
            core.router.lifespan_context(core),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=core), base_url="http://core",
                event_hooks={"request": [observe_request]},
            ) as http,
            httpx.AsyncClient(transport=httpx.MockTransport(model_http)) as models,
        ):
            version = await _approve_evidence(http, version_id, editor)
            settings = ServiceSettings(
                internal_key=INTERNAL_KEY, legal_core_url="http://core",
                hermes_researcher_url="http://researcher", hermes_researcher_key="synthetic",
                hermes_researcher_model="researcher", hermes_reviewer_url="http://reviewer",
                hermes_reviewer_key="synthetic", hermes_reviewer_model="reviewer",
            )
            orchestrator = create_orchestrator(settings=settings, dependencies=ServiceDependencies(
                legal_core=LegalCoreClient(
                    LegalCoreEndpoint("http://core", internal_key=INTERNAL_KEY), client=http,
                ), reasoning=LegalReasoningOrchestrator(
                    researcher=HermesClient(
                        HermesEndpoint("http://researcher", "synthetic", "researcher"),
                        client=models,
                    ), reviewer=HermesClient(
                        HermesEndpoint("http://reviewer", "synthetic", "reviewer"), client=models,
                    ),
                ),
            ))
            gateway = GatewayCoreClient(http)
            jobs = AnalysisJobsClient(http, INTERNAL_KEY)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=orchestrator), base_url="http://orchestrator",
            ) as worker_http:
                async def analyze(case_id):
                    job = await jobs.enqueue(UUID(case_id), ACTOR, 123)
                    assert await worker_once(
                        sessions, worker_http, WorkerSettings("http://orchestrator", INTERNAL_KEY),
                    )
                    completed = await jobs.status(UUID(job["jobId"]), ACTOR)
                    assert completed["state"] == "SUCCEEDED", completed
                    return completed

                async with harness() as (app, _fake_core, sent, dispatch):
                    app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = gateway
                    app.bot_data[JOBS_CLIENT_KEY] = jobs
                    source = await _enter_intake(app, gateway, dispatch, entry, http)
                    case_id = source["case"]["id"]
                    blocked = await analyze(case_id)
                    report = blocked["result"]["report"]["reportJson"]
                    assert blocked["result"]["analysisAllowed"] is False
                    assert _run_status(case_id)[0] == "BLOCKED"
                    assert report["summary"]["analysisAvailability"]["reasonCode"] == (
                        "FACTUAL_CLARIFICATION_REQUIRED"
                    )
                    assert report["clarificationRequests"] == REQUESTS
                    assert report["recommendations"]["status"] == "NOT_AVAILABLE"
                    assert "UNVERIFIED SENTINEL" not in json.dumps(report)
                    assert model_calls == ["researcher"]
                    _, controls = job_message(JobSnapshot.parse(blocked))
                    clarification_buttons = [
                        button.callback_data for row in controls.inline_keyboard for button in row
                        if button.text == "Уточнить факты"
                    ]
                    assert len(clarification_buttons) == 1, (
                        "Blocked questions have no action button"
                    )
                    clarification_callback = clarification_buttons[0]
                    await dispatch(f"analysis:status:{blocked['jobId']}")
                    assert any("Как оплачивалась" in item.get("text", "") for item in sent)
                    original = _source_rows(case_id)
                    assert len(original["case_materials"]) == 1
                    preview = await gateway.clarification_preview(UUID(case_id), ACTOR)
                    denied = await http.get(
                        f"/v1/cases/{case_id}/clarification-drafts/preview",
                        headers=actor_headers(outsider),
                    )
                    assert denied.status_code == 404
                    await _clarification_intent(app, clarification_callback, 10000)
                    drafts = await gateway.list_intake_drafts(ACTOR)
                    draft_id = drafts["items"][0]["id"]
                    draft = await gateway.get_intake_draft(UUID(draft_id), ACTOR)
                    assert [ClarificationRequest.model_validate(item) for item in
                            draft["draftData"]["analysisClarificationRequests"]] == [
                        ClarificationRequest.model_validate(item) for item in REQUESTS
                    ]
                    assert draft["draftData"]["correctionSource"]["reportId"] == (
                        preview["expectedReportId"]
                    )
                    await _clarification_intent(app, clarification_callback, 10001)
                    assert clarification_posts[0] == clarification_posts[1]
                    replayed = await gateway.list_intake_drafts(ACTOR)
                    assert replayed["items"][0]["id"] == draft_id
                    await dispatch(f"case:draft:{draft_id}")
                    payment = "UNKNOWN" if outcome == "unknown" else "PAID"
                    await dispatch(f"case:v2:clarify:SERVICE_PAYMENT_BASIS:0:{payment}")
                    saved = await gateway.get_intake_draft(UUID(draft_id), ACTOR)
                    assert len(saved["draftData"]["analysisClarificationAnswers"]["answers"]) == 1
                    await dispatch("/cancel")

                # A new application has no process-local draft state.
                async with harness() as (app, _fake_core, sent, dispatch):
                    app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = gateway
                    app.bot_data[JOBS_CLIENT_KEY] = jobs
                    assert not app.user_data
                    await dispatch(f"case:draft:{draft_id}")
                    assert "дату" in sent[-1]["text"]
                    date_text = "01.06.2020" if outcome == "earlier-date" else "01.09.2026"
                    await dispatch("text:" + date_text)
                    await dispatch("case:v2:clarify:RESPONSE_PURPOSE:global:ANSWER_REQUEST")
                    final_draft = await gateway.get_intake_draft(UUID(draft_id), ACTOR)
                    assert final_draft["wizardState"] == "SUMMARY"
                    assert final_draft["draftData"]["eventDate"] == {
                        "date": "2026-09-12", "precision": "EXACT",
                    }
                    assert "analysisClarificationAnswers" in final_draft["draftData"]
                    materials = await http.get(
                        f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
                        headers=actor_headers(ACTOR),
                    )
                    assert materials.json() == {"items": []}
                    assert _source_rows(case_id) == original
                    await dispatch("case:v2:summary:confirm")
                    await dispatch(f"case:confirm:{draft_id}")
                    new = await gateway.get_workflow(UUID(draft_id), ACTOR)
                    new_case_id = new["case"]["id"]
                    assert new_case_id != case_id
                    result = (await analyze(new_case_id))["result"]
                    canonical = result["report"]["reportJson"]
                    status, anchor = _run_status(new_case_id)
                    assert anchor.isoformat() == "2026-09-12"
                    if outcome == "positive":
                        assert result["analysisAllowed"] is True
                        assert status == "PASSED"
                        assert canonical["analysis"]["verifierStatus"] == "PASSED"
                        assert canonical["recommendations"]["items"] == [ACTION]
                        evidence = canonical["legalBasis"]["sources"][0]
                        assert evidence["rawSha256"] == version["rawSha256"]
                        assert evidence["fragmentId"] == (
                            projections[-1]["evidence"][0]["fragmentId"]
                        )
                    else:
                        assert result["analysisAllowed"] is False
                        assert status == "BLOCKED"
                        assert canonical["recommendations"]["status"] == "NOT_AVAILABLE"
                        assert canonical.get("clarificationRequests", []) == []
                        if outcome == "earlier-date":
                            assert "EVIDENCE_NOT_EFFECTIVE_ON_SERVICE_DATE" in json.dumps(canonical)
                    expected_calls = ["researcher", "researcher"]
                    if outcome != "unknown":
                        expected_calls.append("reviewer")
                    assert model_calls == expected_calls
                    pdf = await gateway.download_pdf(UUID(result["report"]["id"]), ACTOR)
                    assert hashlib.sha256(pdf).hexdigest() == result["report"]["pdfSha256"]
                    assert _source_rows(case_id) == original
                    assert _source_rows(new_case_id)["case_materials"] == []
                    assert projections[-1]["facts"]["EVENT_DATE"] == {
                        "date": "2026-09-12", "precision": "EXACT",
                    }

    asyncio.run(scenario())
