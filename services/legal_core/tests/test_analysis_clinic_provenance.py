import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from legal_core.corpus_loader import ingest_manifest
from legal_core.database import database_url, owner_database_url
from legal_core.legal_approval import ApprovalAttestation, approve_legal_version
from legal_core.main import create_app
from legal_core.models import LegalVersion, User
from legal_core.risk_policy_approval import RiskPolicyApproval, approve_risk_policy
from legal_core.synthetic_clinic_fixtures import load_synthetic_clinic_versions
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run analysis provenance integration tests",
)

ROOT = Path(__file__).parents[3]
LEGAL_MANIFEST = ROOT / "services/legal_core/tests/fixtures/analysis_provenance_legal_manifest.json"
REVIEWER_TELEGRAM_ID = 9_550_000_001


def _seed_admin(telegram_user_id: int) -> UUID:
    clinic_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO clinics (id,name) VALUES (:id,'Provenance fixture clinic')"),
                {"id": clinic_id},
            )
            connection.execute(
                text("INSERT INTO users (id,telegram_user_id) VALUES (:id,:telegram_id)"),
                {"id": user_id, "telegram_id": telegram_user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO clinic_users (id,clinic_id,user_id,role) "
                    "VALUES (:id,:clinic_id,:user_id,'CLINIC_ADMIN')"
                ),
                {"id": membership_id, "clinic_id": clinic_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO subscription_entitlements "
                    "(clinic_id,user_id,status,plan_code,starts_at) VALUES "
                    "(:clinic_id,:user_id,'ACTIVE','MVP',"
                    "timezone('utc', now())-INTERVAL '1 day')"
                ),
                {"clinic_id": clinic_id, "user_id": user_id},
            )
    finally:
        engine.dispose()
    return clinic_id


def _client() -> TestClient:
    engine = create_async_engine(database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return TestClient(
        create_app(
            session_factory=factory,
            managed_engine=engine,
            enable_draft_retention=False,
        )
    )


def _headers(telegram_user_id: int, idempotency_key: UUID | None = None) -> dict[str, str]:
    result = {"X-Telegram-User-Id": str(telegram_user_id)}
    if idempotency_key is not None:
        result["Idempotency-Key"] = str(idempotency_key)
    return result


async def _prepare_global_analysis_prerequisites() -> None:
    engine = create_async_engine(database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        version_id = await ingest_manifest(factory, LEGAL_MANIFEST)
        async with factory() as session, session.begin():
            reviewer = await session.scalar(
                select(User).where(User.telegram_user_id == REVIEWER_TELEGRAM_ID)
            )
            if reviewer is None:
                reviewer = User(
                    telegram_user_id=REVIEWER_TELEGRAM_ID,
                    display_name="Analysis provenance legal reviewer",
                    system_role="LEGAL_EDITOR",
                )
                session.add(reviewer)
                await session.flush()
            version = await session.get(LegalVersion, version_id)
            assert version is not None
            attestation = ApprovalAttestation(
                reviewer_telegram_user_id=REVIEWER_TELEGRAM_ID,
                version_id=version.id,
                expected_sha256=version.raw_sha256,
                expected_normalized_sha256=version.normalized_sha256,
                expected_fragments_sha256=version.fragments_sha256,
                expected_effective_from=version.effective_from,
                expected_effective_to=version.effective_to,
                source_is_official=True,
                artifact_is_complete=True,
                effective_dates_verified=True,
                fragments_verified=True,
            )

        await approve_legal_version(factory, attestation)
        await approve_risk_policy(
            factory,
            RiskPolicyApproval(
                reviewer_telegram_user_id=REVIEWER_TELEGRAM_ID,
                version=1,
                high_demand_threshold_kopecks=10_000_000,
                incident_triggers_reviewed=True,
                monetary_threshold_reviewed=True,
                escalation_rules_reviewed=True,
            ),
        )
    finally:
        await engine.dispose()


def _workflow_payload() -> dict[str, object]:
    return {
        "intakeSchemaVersion": "dental-case-intake.v1",
        "locale": "ru-RU",
        "facts": [
            {
                "factKey": "INCIDENT_TYPES",
                "valueType": "ENUM_SET",
                "value": {"values": ["CROWN_PROBLEM"]},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "PRIMARY_INCIDENT_TYPE",
                "valueType": "ENUM",
                "value": {"value": "CROWN_PROBLEM"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "SERVICE_TYPE",
                "valueType": "TEXT",
                "value": {"text": "Имплантация"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "SERVICE_DATE",
                "valueType": "DATE",
                "value": {"date": "2026-08-01", "precision": "EXACT"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "INCIDENT_DATE",
                "valueType": "DATE",
                "value": {"date": "2026-08-21", "precision": "EXACT"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "CLAIM_DATE",
                "valueType": "DATE",
                "value": {"date": "2026-08-22", "precision": "EXACT"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "PROBLEM_SUMMARY",
                "valueType": "TEXT",
                "value": {"text": "Пациент сообщил о проблеме после имплантации."},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "PATIENT_DEMAND",
                "valueType": "ENUM_SET",
                "value": {"values": ["NO_SPECIFIC_DEMAND"]},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "FORMAL_CLAIM",
                "valueType": "BOOLEAN",
                "value": {"boolean": False},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "HARM_CLAIMED",
                "valueType": "BOOLEAN",
                "value": {"boolean": False},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "LAWYER_CONTACT",
                "valueType": "BOOLEAN",
                "value": {"state": "NO"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "REGULATOR_OR_COURT",
                "valueType": "BOOLEAN",
                "value": {"boolean": False},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "REGULATOR_THREAT",
                "valueType": "BOOLEAN",
                "value": {"state": "NO"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "CLINIC_DOCUMENTS",
                "valueType": "DOCUMENT_INVENTORY",
                "value": {"CONTRACT": "AVAILABLE", "MEDICAL_RECORD": "AVAILABLE"},
                "sourceType": "USER_STATEMENT",
            },
        ],
    }


def _install_approved_warranty(api: TestClient, telegram_user_id: int) -> str:
    fixture = next(
        item for item in load_synthetic_clinic_versions() if item.document_key == "warranty-main"
    )
    created = api.post(
        "/v1/clinic-documents",
        headers=_headers(telegram_user_id),
        json={
            "documentKey": fixture.document_key,
            "documentType": fixture.document_type,
            "title": fixture.title,
        },
    )
    assert created.status_code == 201
    version = api.post(
        f"/v1/clinic-documents/{created.json()['id']}/text-versions",
        headers=_headers(telegram_user_id),
        json={
            "sourceFilename": fixture.filename,
            "normalizedText": fixture.text,
            "validFrom": fixture.valid_from.isoformat() if fixture.valid_from else None,
            "validTo": fixture.valid_to.isoformat() if fixture.valid_to else None,
        },
    )
    assert version.status_code == 201
    approved = api.post(
        f"/v1/clinic-documents/versions/{version.json()['id']}/approval-events",
        headers=_headers(telegram_user_id),
        json={
            "decision": "APPROVED",
            "reasonCode": "SYNTHETIC_FIXTURE_REVIEW_PASSED",
        },
    )
    assert approved.status_code == 201
    return str(version.json()["id"])


def test_blocked_clinic_document_invalidates_frozen_analysis_context() -> None:
    asyncio.run(_prepare_global_analysis_prerequisites())
    admin = 9_560_000_001 + uuid4().int % 10_000_000
    _seed_admin(admin)

    with _client() as api:
        warranty_version_id = _install_approved_warranty(api, admin)
        workflow = api.post(
            f"/v1/telegram-case-workflows/{uuid4()}/submissions",
            headers=_headers(admin),
            json=_workflow_payload(),
        )
        assert workflow.status_code == 201
        case_id = workflow.json()["case"]["id"]

        frozen = api.get(
            f"/v1/cases/{case_id}/analysis-context",
            headers=_headers(admin),
        )
        assert frozen.status_code == 200
        frozen_payload = frozen.json()
        assert frozen_payload["evidence"]
        assert any(
            item["versionId"] == warranty_version_id
            for item in frozen_payload["clinicDocumentContext"]
        )
        official_evidence_ids = {item["fragmentId"] for item in frozen_payload["evidence"]}
        clinic_context_ids = {
            item["fragmentId"] for item in frozen_payload["clinicDocumentContext"]
        }
        assert official_evidence_ids.isdisjoint(clinic_context_ids)

        blocked = api.post(
            f"/v1/clinic-documents/versions/{warranty_version_id}/approval-events",
            headers=_headers(admin),
            json={
                "decision": "BLOCKED",
                "reasonCode": "SYNTHETIC_FIXTURE_REVOKED",
            },
        )
        assert blocked.status_code == 201

        refreshed = api.get(
            f"/v1/cases/{case_id}/analysis-context",
            headers=_headers(admin),
        )
        assert refreshed.status_code == 200
        refreshed_payload = refreshed.json()
        assert (
            refreshed_payload["clinicDocumentContextTraceSha256"]
            != frozen_payload["clinicDocumentContextTraceSha256"]
        )
        assert all(
            item["versionId"] != warranty_version_id
            for item in refreshed_payload["clinicDocumentContext"]
        )

        evidence_id = frozen_payload["evidence"][0]["fragmentId"]
        stale_submission = api.post(
            f"/v1/cases/{case_id}/analysis-submissions",
            headers=_headers(admin, uuid4()),
            json={
                "asOfDate": frozen_payload["asOfDate"],
                "expectedFactSnapshotSha256": frozen_payload["factSnapshotSha256"],
                "expectedEvidenceTraceSha256": frozen_payload["evidenceTraceSha256"],
                "expectedClinicDocumentContextTraceSha256": (
                    frozen_payload["clinicDocumentContextTraceSha256"]
                ),
                "expectedRiskPolicyVersion": frozen_payload["riskPolicyVersion"],
                "claims": [
                    {
                        "claimId": "c1",
                        "kind": "LEGAL",
                        "text": "Синтетический вывод только для stale-check.",
                        "evidenceFragmentIds": [evidence_id],
                        "requiredFactKeys": ["FORMAL_CLAIM"],
                    }
                ],
                "semanticReviews": [
                    {
                        "claimId": "c1",
                        "verdict": "SUPPORTED",
                        "reviewedFragmentIds": [evidence_id],
                    }
                ],
            },
        )
        assert stale_submission.status_code == 409
        assert stale_submission.json()["error"]["code"] == "ANALYSIS_CONTEXT_STALE"
