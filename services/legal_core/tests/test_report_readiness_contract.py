from datetime import UTC, datetime
from uuid import UUID

from legal_core.contracts import CanonicalReport, CaseStatus, ClinicDocumentReadinessCard
from legal_core.reports import build_intake_report


def _legacy_payload() -> dict[str, object]:
    return {
        "schemaVersion": "dental-case-report.v1",
        "reportId": "00000000-0000-0000-0000-000000000010",
        "reportVersion": 1,
        "generatedAt": "2026-08-31T10:00:00Z",
        "case": {
            "id": "00000000-0000-0000-0000-000000000020",
            "publicNumber": "DL-2026-000001",
            "status": "ANALYSIS_BLOCKED",
        },
        "summary": {
            "neutralDescription": "Синтетический кейс.",
            "incidentTypes": [],
            "analysisAvailability": {
                "status": "BLOCKED",
                "reasonCode": "VERIFICATION_FAILED",
            },
        },
        "facts": {},
        "missingFacts": [],
        "recommendations": {"status": "NOT_AVAILABLE", "items": []},
        "draftResponse": {
            "status": "NOT_AVAILABLE",
            "text": None,
            "isDraft": True,
            "humanApprovalRequired": True,
            "reasonCode": None,
            "policyVersion": None,
        },
        "legalBasis": {"status": "NOT_AVAILABLE", "sources": []},
        "clinicDocuments": {"status": "NOT_USED", "sources": []},
        "risk": None,
        "analysis": None,
        "factSnapshotSha256": "a" * 64,
        "disclaimer": "Synthetic disclaimer",
    }


def test_legacy_report_without_readiness_field_still_validates() -> None:
    report = CanonicalReport.model_validate(_legacy_payload())

    assert report.schema_version == "dental-case-report.v1"
    assert report.clinic_document_readiness == []


def test_readiness_snapshot_contains_metadata_only_and_is_non_blocking() -> None:
    report = build_intake_report(
        report_id=UUID("00000000-0000-0000-0000-000000000010"),
        case_id=UUID("00000000-0000-0000-0000-000000000020"),
        public_number="DL-2026-000001",
        case_status=CaseStatus.ANALYSIS_BLOCKED,
        report_version=2,
        generated_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        facts={},
        missing_facts=[],
        block_reason_code="VERIFICATION_FAILED",
    ).model_copy(
        update={
            "clinic_document_readiness": [
                ClinicDocumentReadinessCard(
                    expectationCode="WARRANTY_POLICY",
                    importance="SCENARIO",
                    acceptedDocumentTypes=["WARRANTY_POLICY"],
                    reasonCode="WARRANTY_SENSITIVE_SERVICE_OR_INCIDENT",
                    status="NOT_AVAILABLE",
                    matchedDocumentKeys=[],
                    analysisBlocking=False,
                )
            ]
        }
    )

    payload = report.model_dump(mode="json", by_alias=True)
    readiness = payload["clinicDocumentReadiness"][0]
    assert readiness == {
        "expectationCode": "WARRANTY_POLICY",
        "importance": "SCENARIO",
        "acceptedDocumentTypes": ["WARRANTY_POLICY"],
        "reasonCode": "WARRANTY_SENSITIVE_SERVICE_OR_INCIDENT",
        "status": "NOT_AVAILABLE",
        "matchedDocumentKeys": [],
        "analysisBlocking": False,
    }
    serialized = str(payload)
    assert "normalizedText" not in serialized
    assert "fragmentText" not in serialized
