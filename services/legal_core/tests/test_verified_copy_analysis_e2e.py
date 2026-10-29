"""Human approval -> real HTTP services -> durable worker -> immutable report.

Only the two external model HTTP responses are synthetic. Core, orchestrator, their clients,
SQL approval/retrieval guards, queue/lease, verifier, report rendering and persistence are real.
The guided v2 path currently records its exact fail-closed risk-policy blocker.
"""

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
from agent_orchestrator.main import ServiceDependencies, ServiceSettings
from agent_orchestrator.main import create_app as create_orchestrator
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.analysis_job_worker import WorkerSettings, worker_once
from legal_core.corpus_loader import ingest_manifest
from legal_core.database import database_url, owner_database_url
from legal_core.main import create_app
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from legal_core.risk_policy_approval import RiskPolicyApproval, approve_risk_policy
from legal_core.runtime_db_role import provision_runtime_role
from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers, complete_fact_batch, seed_admin
from test_legal_editor_workspace_api import _seed_user, _write_manifest

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)
ROOT = Path(__file__).parents[3]
INTERNAL_KEY = "synthetic-analysis-e2e-internal-" + "x" * 32
EDITOR_KEY = "synthetic-analysis-e2e-editor-" + "y" * 32
EVIDENCE = (
    "Синтетическая учебная норма: платные медицинские услуги. "
    "Зафиксировать обращение пациента во внутреннем журнале."
)
ACTION = "Зафиксировать обращение пациента во внутреннем журнале."
CASE_MATERIAL_TEXT = "Учебное приложение: клиника предложила повторный осмотр без оплаты."


@pytest.fixture
def isolated_database(monkeypatch):
    """Do not leave approved base-query evidence/default policy in other integration tests."""
    assert os.environ.get("POSTGRES_DB", "").startswith("dental_legal_test")
    identifier = "dental_legal_test_copy_e2e_" + uuid4().hex[:16]
    owner = create_engine(
        owner_database_url().set(drivername="postgresql+psycopg"),
        isolation_level="AUTOCOMMIT",
    )
    with owner.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{identifier}"')
    try:
        monkeypatch.setenv("POSTGRES_DB", identifier)
        monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://orchestrator")
        monkeypatch.setenv("AGENT_INTERNAL_KEY", INTERNAL_KEY)
        monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", EDITOR_KEY)
        # Exercise production lexical retrieval without inheriting a real external embedding key.
        for suffix in ("BASE_URL", "MODEL", "MODEL_KEY", "DIMENSIONS", "API_KEY"):
            monkeypatch.delenv(f"LEGAL_EMBEDDING_{suffix}", raising=False)
        provision_runtime_role()
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        provision_runtime_role()
        yield
    finally:
        # Exact generated test-only database; never the caller's shared integration database.
        with owner.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{identifier}" WITH (FORCE)')
        owner.dispose()


@pytest.mark.parametrize(
    "intake_mode",
    [
        "legacy",
        "guided-v2",
        "guided-v2-factual-low",
        "guided-v2-factual-medium",
        "guided-v2-factual-clinic-doc",
        "guided-v2-factual-case-file",
        "guided-v2-factual-clinic-doc-case-file",
    ],
)
def test_approved_copy_analysis_distinguishes_legacy_report_from_guided_v2_block(
    isolated_database, tmp_path: Path, intake_mode: str, monkeypatch
) -> None:
    with_clinic_documents = "clinic-doc" in intake_mode
    with_case_materials = "case-file" in intake_mode
    pre_report_mode = with_clinic_documents or with_case_materials
    if with_case_materials:
        monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "1")
    owner_id = 71_000_000_000 + uuid4().int % 1_000_000_000
    editor_id = owner_id + 2_000_000_000
    clinic_id, _ = seed_admin(owner_id, role="CLINIC_OWNER")
    _seed_user(editor_id, system_role="LEGAL_EDITOR")
    manifest_path = _write_manifest(
        tmp_path, fragment_text=EVIDENCE, effective_from="2026-08-04", effective_to="2027-08-04"
    )
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        {
            "manifest_version": "dental-legal-corpus.v3",
            "source_key": "consultant-plus",
            "source_name": "КонсультантПлюс",
            "source_trust_level": "VERIFIED_COPY",
            "source_base_url": "https://www.consultant.ru/",
            "source_url": "https://www.consultant.ru/document/cons_doc_LAW_synthetic/",
            "allowed_hosts": ["www.consultant.ru"],
            "artifact_kind": "THIRD_PARTY_VERIFIED_COPY",
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    model_calls = []

    def model_http(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        projection = json.loads(body["messages"][-1]["content"])
        assert len(projection["evidence"]) == 1
        evidence = projection["evidence"][0]
        assert evidence["text"] == EVIDENCE
        assert evidence["sourceUrl"].startswith("https://www.consultant.ru/")
        assert evidence["effectiveFrom"] == "2026-08-04"
        fragment_id = evidence["fragmentId"]
        model_calls.append(body["model"])
        if body["model"] == "researcher":
            if with_clinic_documents:
                assert len(projection["clinicDocumentContext"]) == 1
                document = projection["clinicDocumentContext"][0]
                assert document["documentType"] == "PATIENT_MEMO_POST_IMPLANT"
                assert document["text"] == "Имплант: сохраняйте памятку, выданную клиникой."
            if with_case_materials:
                attachments = projection["caseMaterialContext"]
                assert len(attachments) == 1
                assert attachments[0]["contextKind"] == "CASE_MATERIAL_CONTEXT"
                assert attachments[0]["displayName"] == "Материал 1"
                assert attachments[0]["text"] == CASE_MATERIAL_TEXT
                assert not ({"materialId", "fragmentId", "rawSha256", "textSha256"}
                            & attachments[0].keys())
                assert "synthetic-case-note" not in json.dumps(projection)
            result = {
                "claims": [
                    {
                        "claimId": "record-appeal",
                        "kind": "ACTION",
                        "text": ACTION,
                        "evidenceFragmentIds": [fragment_id],
                        "requiredFactKeys": [],
                    }
                ],
                "internalRecommendations": [],
                "patientDraft": None,
            }
        else:
            assert body["model"] == "reviewer"
            assert projection["claims"][0]["evidenceFragmentIds"] == [fragment_id]
            assert "clinicDocumentContext" not in projection
            assert "caseMaterialContext" not in projection
            result = {
                "reviews": [
                    {
                        "claimId": "record-appeal",
                        "verdict": "SUPPORTED",
                        "reviewedFragmentIds": [fragment_id],
                    }
                ]
            }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]},
        )

    async def scenario():
        engine = create_async_engine(database_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        version_id = await ingest_manifest(sessions, manifest_path)
        await approve_risk_policy(
            sessions,
            RiskPolicyApproval(
                reviewer_telegram_user_id=editor_id,
                high_demand_threshold_kopecks=5_000_000,
                incident_triggers_reviewed=True,
                monetary_threshold_reviewed=True,
                escalation_rules_reviewed=True,
                version=1 if "factual" in intake_mode else 2,
                early_triage_enabled="factual" not in intake_mode,
            ),
        )
        if "factual" in intake_mode:
            await approve_risk_policy(
                sessions,
                RiskPolicyApproval(
                    reviewer_telegram_user_id=editor_id,
                    version=3,
                    high_demand_threshold_kopecks=5_000_000,
                    incident_triggers_reviewed=True,
                    monetary_threshold_reviewed=True,
                    escalation_rules_reviewed=True,
                    early_triage_enabled=True,
                    supersede_approved=True,
                    guided_v2_explicit_signals_enabled=True,
                    direct_v1_supersession_reviewed=True,
                    factual_safety_intake_enabled=True,
                    factual_safety_intake_reviewed=True,
                ),
            )
        core = create_app(
            session_factory=sessions,
            managed_engine=engine,
            enable_draft_retention=False,
            enable_analysis_worker=False,
        )
        async with (
            core.router.lifespan_context(core),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=core), base_url="http://core"
            ) as core_http,
            httpx.AsyncClient(transport=httpx.MockTransport(model_http)) as models,
        ):
            settings = ServiceSettings(
                internal_key=INTERNAL_KEY,
                legal_core_url="http://core",
                hermes_researcher_url="http://researcher",
                hermes_researcher_key="synthetic",
                hermes_researcher_model="researcher",
                hermes_reviewer_url="http://reviewer",
                hermes_reviewer_key="synthetic",
                hermes_reviewer_model="reviewer",
            )
            orchestrator = create_orchestrator(
                settings=settings,
                dependencies=ServiceDependencies(
                    legal_core=LegalCoreClient(
                        LegalCoreEndpoint("http://core", internal_key=INTERNAL_KEY),
                        client=core_http,
                    ),
                    reasoning=LegalReasoningOrchestrator(
                        researcher=HermesClient(
                            HermesEndpoint("http://researcher", "synthetic", "researcher"),
                            client=models,
                        ),
                        reviewer=HermesClient(
                            HermesEndpoint("http://reviewer", "synthetic", "reviewer"),
                            client=models,
                        ),
                    ),
                ),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=orchestrator), base_url="http://orchestrator"
            ) as worker_http:
                worker_settings = WorkerSettings("http://orchestrator", INTERNAL_KEY)
                if intake_mode.startswith("guided-v2"):
                    guided_data = {
                        "intakeVersion": 2,
                        "incomingKind": "COMPLAINT",
                        "incomingSourceStatus": "NOT_ATTACHED",
                        "situationAreas": ["TREATMENT"],
                        "affectedServices": ["терапевтическое лечение"],
                        "eventSummary": "После лечения пациент сообщил о дискомфорте.",
                        "eventDate": {"date": "2026-09-12", "precision": "EXACT"},
                        "conflictStage": "FIRST",
                        "clinicActions": ["INVITED_FOR_EXAMINATION"],
                        "healthSignals": ["NO_KNOWN_INFORMATION"],
                        "caseMaterialsStatus": "NOT_ATTACHED",
                    }
                    if "factual" in intake_mode:
                        screening = {
                            "schemaVersion": SCREENING_VERSION,
                            **dict.fromkeys(SCREENING_FIELDS, "NO"),
                            "amount": "NOT_REQUESTED",
                        }
                        if intake_mode.endswith("medium"):
                            screening.update(
                                moneyRequested="YES",
                                amount={
                                    "amountKopecks": 4_999_999,
                                    "currency": "RUB",
                                },
                            )
                        guided_data["safetyScreening"] = screening
                    if pre_report_mode:
                        guided_data["affectedServices"] = ["Имплантация зуба"]
                        guided_data["analysisClarificationAnswers"] = {"answers": [
                            {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0,
                             "value": "PAID"},
                            {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
                             "value": {"date": "2026-09-12", "precision": "EXACT"}},
                            {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None,
                             "value": "ANSWER_REQUEST"},
                        ]}
                    workflow_id = uuid4()
                    material_id = None
                    if with_case_materials:
                        draft = await core_http.post(
                            "/v1/telegram-intake-drafts", headers=actor_headers(owner_id, uuid4()),
                            json={"intakeSchemaVersion": "dental-case-intake.v2"},
                        )
                        assert draft.status_code == 201, draft.text
                        workflow_id = draft.json()["id"]
                        uploaded = await core_http.post(
                            f"/v1/telegram-intake-drafts/{workflow_id}/case-materials",
                            headers={**actor_headers(owner_id, uuid4()),
                                     "X-Source-Filename": "synthetic-case-note.txt",
                                     "Content-Type": "text/plain"},
                            params={"include_in_analysis": "true"},
                            content=CASE_MATERIAL_TEXT.encode(),
                        )
                        assert uploaded.status_code == 201, uploaded.text
                        assert uploaded.json()["material"]["analysisUseAllowed"] is True
                        material_id = uploaded.json()["material"]["id"]
                        guided_data["caseMaterialsStatus"] = "ATTACHED"
                    workflow = await core_http.post(
                        f"/v1/telegram-case-workflows/{workflow_id}/submissions",
                        headers=actor_headers(owner_id),
                        json={
                            **({"receiptVersion": 2} if pre_report_mode else {}),
                            "intakeSchemaVersion": "dental-case-intake.v2",
                            "locale": "ru-RU",
                            "facts": facts_from_v2_data(guided_data),
                        },
                    )
                    assert workflow.status_code == 201, workflow.text
                    case_id = workflow.json()["case"]["id"]
                    assert workflow.json()["case"]["status"] == "ANALYSIS_BLOCKED"
                    initial_report_count = 0 if pre_report_mode else 1
                else:
                    created = await core_http.post(
                        "/v1/cases",
                        headers=actor_headers(owner_id, uuid4()),
                        json={
                            "intakeSchemaVersion": "dental-case-intake.v1",
                            "channel": "TELEGRAM",
                        },
                    )
                    assert created.status_code == 201, created.text
                    case_id = created.json()["id"]
                    batch = complete_fact_batch()
                    for fact in batch["facts"]:
                        if fact["factKey"] in {"CLAIM_DATE", "INCIDENT_DATE", "SERVICE_DATE"}:
                            fact["value"] = {"date": "2026-09-12", "precision": "EXACT"}
                    facts = await core_http.post(
                        f"/v1/cases/{case_id}/facts",
                        headers=actor_headers(owner_id, uuid4()),
                        json=batch,
                    )
                    assert facts.status_code == 200, facts.text
                    assert facts.json()["missingFacts"] == []
                    confirmed = await core_http.post(
                        f"/v1/cases/{case_id}/intake-finalizations",
                        headers=actor_headers(owner_id, uuid4()),
                        json={},
                    )
                    assert confirmed.status_code == 200, confirmed.text
                    assert confirmed.json()["status"] == "ANALYSIS_BLOCKED"
                    initial_report_count = 0

                async def enqueue():
                    response = await core_http.post(
                        f"/v1/cases/{case_id}/analysis-jobs",
                        headers=actor_headers(owner_id),
                        json={"messageId": 123},
                    )
                    assert response.status_code == 202, response.text
                    assert response.json()["state"] == "QUEUED"
                    return response.json()["jobId"]

                blocked_job = None
                if pre_report_mode:
                    blocked = await core_http.get(
                        f"/v1/cases/{case_id}/analysis-context", headers={
                            **actor_headers(owner_id), "X-Agent-Internal-Key": INTERNAL_KEY,
                        },
                    )
                    assert blocked.status_code == 409, blocked.text
                    assert blocked.json()["error"]["code"] == "LEGAL_EVIDENCE_UNAVAILABLE"
                else:
                    blocked_job = await enqueue()
                    assert await worker_once(sessions, worker_http, worker_settings)
                    blocked = await core_http.get(
                        f"/v1/analysis-jobs/{blocked_job}", headers=actor_headers(owner_id)
                    )
                    assert blocked.json()["state"] == "FAILED", blocked.text
                    assert blocked.json()["errorCode"] == "LEGAL_EVIDENCE_UNAVAILABLE", blocked.text
                assert model_calls == []

                editor_headers = {
                    "X-Telegram-User-Id": str(editor_id),
                    "X-Legal-Editor-Gateway-Key": EDITOR_KEY,
                    "Idempotency-Key": str(uuid4()),
                }
                detail = await core_http.get(
                    f"/v1/legal/review-queue/{version_id}", headers=editor_headers
                )
                assert detail.status_code == 200 and detail.json()["approvalEligible"], detail.text
                version = detail.json()
                approval = await core_http.post(
                    f"/v1/legal/review-queue/{version_id}/approval-events",
                    headers=editor_headers,
                    json={
                        "expectedSha256": version["rawSha256"],
                        "expectedNormalizedSha256": version["normalizedSha256"],
                        "expectedFragmentsSha256": version["fragmentsSha256"],
                        "expectedEffectiveFrom": version["effectiveFrom"],
                        "expectedEffectiveTo": version["effectiveTo"],
                        "sourceIsOfficial": False,
                        "officialTextCompared": True,
                        "artifactIsComplete": True,
                        "effectiveDatesVerified": True,
                        "fragmentsVerified": True,
                    },
                )
                assert approval.status_code == 200, approval.text
                clinic_version_id = None
                if with_clinic_documents:
                    clinic_document = await core_http.post(
                        "/v1/clinic-documents", headers=actor_headers(owner_id), json={
                            "documentKey": "post-treatment-memo",
                            "documentType": "PATIENT_MEMO_POST_IMPLANT",
                            "title": "Инструкция после процедуры",
                        },
                    )
                    assert clinic_document.status_code == 201, clinic_document.text
                    raw_clinic_text = "Имплант: сохраняйте памятку, выданную клиникой."
                    clinic_version = await core_http.post(
                        f"/v1/clinic-documents/{clinic_document.json()['id']}/file-versions",
                        headers={**actor_headers(owner_id), "X-Source-Filename": "memo.txt",
                                 "Content-Type": "text/plain"},
                        params={"valid_from": "2026-09-01", "valid_to": "2026-10-01"},
                        content=raw_clinic_text.encode(),
                    )
                    assert clinic_version.status_code == 201, clinic_version.text
                    clinic_version_id = clinic_version.json()["id"]
                    assert clinic_version.json()["rawSha256"] == hashlib.sha256(
                        raw_clinic_text.encode(),
                    ).hexdigest()
                    context_headers = {
                        **actor_headers(owner_id), "X-Agent-Internal-Key": INTERNAL_KEY,
                    }
                    pending_context = await core_http.get(
                        f"/v1/cases/{case_id}/analysis-context", headers=context_headers,
                    )
                    assert pending_context.status_code == 200, pending_context.text
                    assert pending_context.json()["clinicDocumentContext"] == []
                    clinic_review = await core_http.post(
                        f"/v1/clinic-documents/versions/{clinic_version_id}/approval-events",
                        headers=actor_headers(owner_id),
                        json={"decision": "APPROVED", "reasonCode": "SYNTHETIC_REVIEW_PASSED"},
                    )
                    assert clinic_review.status_code == 201, clinic_review.text
                    outside = await core_http.get(
                        "/v1/clinic-documents/fragments", headers=actor_headers(owner_id),
                        params={"query": "имплант", "as_of_date": "2026-08-31"},
                    )
                    assert outside.status_code == 200 and outside.json()["items"] == []
                for path in (
                    "/v1/legal/library?as_of_date=2026-09-12",
                    "/v1/legal/fragments?query=платные%20медицинские%20услуги&as_of_date=2026-09-12",
                ):
                    response = await core_http.get(path, headers=actor_headers(owner_id))
                    assert response.status_code == 200, response.text
                    assert {item["versionId"] for item in response.json()["items"]} == {
                        str(version_id)
                    }
                context = await core_http.get(
                    f"/v1/cases/{case_id}/analysis-context",
                    headers={
                        **actor_headers(owner_id),
                        "X-Agent-Internal-Key": INTERNAL_KEY,
                    },
                )
                assert context.status_code == 200, context.text
                assert context.json()["asOfDate"] == "2026-09-12"
                assert context.json()["evidence"][0]["versionId"] == str(version_id)
                if clinic_version_id is not None:
                    assert len(context.json()["clinicDocumentContext"]) == 1
                    assert context.json()["clinicDocumentContext"][0]["versionId"] == (
                        clinic_version_id
                    )
                if with_case_materials:
                    file_context = context.json()["caseMaterialContext"]
                    assert len(file_context) == 1
                    assert file_context[0]["materialId"] == material_id
                    assert file_context[0]["text"] == CASE_MATERIAL_TEXT
                    assert context.json()["caseMaterialContextTraceSha256"]
                for outside_date in ("2026-08-03", "2027-08-04"):
                    outside = await core_http.get(
                        "/v1/legal/fragments",
                        headers=actor_headers(owner_id),
                        params={"query": "платные медицинские услуги", "as_of_date": outside_date},
                    )
                    assert outside.status_code == 200 and outside.json()["items"] == []

                job_id = await enqueue()
                assert job_id != blocked_job
                assert await worker_once(sessions, worker_http, worker_settings)
                completed = await core_http.get(
                    f"/v1/analysis-jobs/{job_id}", headers=actor_headers(owner_id)
                )
                assert completed.json()["state"] == "SUCCEEDED", (completed.text, model_calls)
                result = completed.json()["result"]
                if pre_report_mode:
                    assert completed.json()["resultVersion"] == 2
                    assert result["status"] == "READY", result
                else:
                    assert result["escalationRequired"] is False
                report = result["report"]
                canonical = report["reportJson"]
                if intake_mode == "guided-v2":
                    assert result["analysisAllowed"] is False
                    assert result["riskLevel"] == "UNAVAILABLE"
                    assert canonical["summary"]["analysisAvailability"]["reasonCode"] == (
                        "HARM_CLAIMED_UNKNOWN"
                    )
                    assert canonical["legalBasis"]["status"] == "NOT_AVAILABLE"
                    assert canonical["recommendations"]["status"] == "NOT_AVAILABLE"
                else:
                    expected_level = "MEDIUM" if intake_mode.endswith("medium") else "LOW"
                    assert result["analysisAllowed"] is True
                    assert canonical["risk"]["level"] == expected_level
                    if not pre_report_mode:
                        assert result["riskLevel"] == expected_level
                    assert canonical["legalBasis"]["status"] == "AVAILABLE"
                    source = canonical["legalBasis"]["sources"][0]
                    assert source["fragmentId"] == context.json()["evidence"][0]["fragmentId"]
                    assert source["rawSha256"] == version["rawSha256"]
                    assert canonical["analysis"]["verifierStatus"] == "PASSED"
                    assert canonical["recommendations"]["items"] == [ACTION]
                    if clinic_version_id is not None:
                        assert canonical["clinicDocuments"]["status"] == "USED"
                        assert canonical["clinicDocuments"]["sources"][0]["versionId"] == (
                            clinic_version_id
                        )
                        assert canonical["clinicDocuments"]["sources"][0]["documentTitle"] == (
                            "Инструкция после процедуры"
                        )
                if with_case_materials:
                    assert canonical["caseMaterials"]["status"] == "USED"
                    file_source = canonical["caseMaterials"]["sources"][0]
                    assert file_source["materialId"] == material_id
                    assert file_source["displayName"] == "Материал 1"
                    assert file_source["rawSha256"] == hashlib.sha256(
                        CASE_MATERIAL_TEXT.encode(),
                    ).hexdigest()
                    assert canonical["caseMaterialContextTraceSha256"] == (
                        context.json()["caseMaterialContextTraceSha256"]
                    )
                    assert file_source["fragmentId"] not in {
                        source["fragmentId"] for source in canonical["legalBasis"]["sources"]
                    }
                assert model_calls == ["researcher", "reviewer"]
                pdf = await core_http.get(
                    f"/v1/reports/{report['id']}/pdf", headers=actor_headers(owner_id)
                )
                assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-"), pdf.text[:100]
                assert hashlib.sha256(pdf.content).hexdigest() == report["pdfSha256"]
                if with_case_materials:
                    pdf_text = subprocess.run(
                        ["pdftotext", "-", "-"], input=pdf.content, capture_output=True,
                        timeout=10, check=True,
                    ).stdout.decode()
                    assert "Приложенные документы" in pdf_text
                    assert "Материал 1" in pdf_text
                    assert "synthetic-case-note" not in pdf_text
                    assert CASE_MATERIAL_TEXT not in json.dumps(canonical, ensure_ascii=False)
                assert not await worker_once(sessions, worker_http, worker_settings)
                assert model_calls == ["researcher", "reviewer"]
                discovery = await core_http.get(
                    "/v1/internal/analysis-job-notifications",
                    headers={"X-Agent-Internal-Key": INTERNAL_KEY},
                )
                assert discovery.status_code == 200, discovery.text
                assert any(item["jobId"] == job_id for item in discovery.json()["items"])
                acknowledged = await core_http.post(
                    f"/v1/analysis-jobs/{job_id}/notification-ack", headers=actor_headers(owner_id)
                )
                assert acknowledged.status_code == 204

                async with sessions() as session:
                    await session.execute(
                        text("SELECT set_config('app.current_clinic_id',:c,true)"),
                        {"c": str(clinic_id)},
                    )
                    assert (
                        await session.scalar(
                            text("SELECT count(*) FROM case_analysis_runs WHERE case_id=:id"),
                            {"id": case_id},
                        )
                        == 1
                    )
                    assert (
                        await session.scalar(
                            text("SELECT count(*) FROM case_reports WHERE case_id=:id"),
                            {"id": case_id},
                        )
                        == initial_report_count + 1
                    )
                    risk = (
                        await session.execute(
                            text(
                                "SELECT level, reason_codes_json FROM case_risk_assessments "
                                "WHERE case_id=:id ORDER BY created_at DESC LIMIT 1"
                            ),
                            {"id": case_id},
                        )
                    ).one()
                    if intake_mode == "guided-v2":
                        assert risk == ("UNAVAILABLE", ["HARM_CLAIMED_UNKNOWN"])
                    else:
                        assert risk == (
                            ("MEDIUM", ["PATIENT_DEMAND_REQUIRES_REVIEW"])
                            if intake_mode.endswith("medium")
                            else ("LOW", ["NO_ESCALATION_TRIGGER"])
                        )
                    assert (
                        await session.scalar(
                            text(
                                "SELECT count(*) FROM idempotency_records WHERE key=:key "
                                "AND scope=:scope AND state='SUCCEEDED'"
                            ),
                            {"key": job_id, "scope": f"cases:{case_id}:"
                             + ("preparation-submissions" if pre_report_mode
                                else "analysis-submissions")},
                        )
                        == 1
                    )

    asyncio.run(scenario())
