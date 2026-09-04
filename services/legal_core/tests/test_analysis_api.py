from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from legal_core.analysis_api import (
    _analysis_date,
    _domain_claims,
    _semantic_reviews,
    _verified_action_items,
)
from legal_core.analysis_contracts import AnalysisSubmissionRequest, AnalysisSubmissionResponse
from legal_core.api_contracts import ReportResponse
from legal_core.contracts import FactKey
from legal_core.verifier import ClaimKind, SemanticVerdict, VerificationResult


FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000001")


def _submission() -> AnalysisSubmissionRequest:
    return AnalysisSubmissionRequest(
        asOfDate=date(2026, 8, 31),
        expectedFactSnapshotSha256="a" * 64,
        expectedEvidenceTraceSha256="b" * 64,
        expectedClinicDocumentContextTraceSha256="c" * 64,
        expectedRiskPolicyVersion="dental-risk.v1",
        claims=[
            {
                "claimId": "action-1",
                "kind": "ACTION",
                "text": "Зафиксировать обращение пациента.",
                "evidenceFragmentIds": [FRAGMENT_ID],
                "requiredFactKeys": ["FORMAL_CLAIM"],
            }
        ],
        semanticReviews=[
            {
                "claimId": "action-1",
                "verdict": "SUPPORTED",
                "reviewedFragmentIds": [FRAGMENT_ID],
            }
        ],
    )


def test_analysis_date_prefers_claim_then_incident_then_service() -> None:
    facts = {
        FactKey.SERVICE_DATE: {"precision": "EXACT", "date": "2026-01-10"},
        FactKey.INCIDENT_DATE: {"precision": "EXACT", "date": "2026-02-10"},
        FactKey.CLAIM_DATE: {"precision": "EXACT", "date": "2026-03-10"},
    }

    assert _analysis_date(facts) == date(2026, 3, 10)


def test_analysis_date_ignores_unknown_or_invalid_dates() -> None:
    facts = {
        FactKey.CLAIM_DATE: {"precision": "UNKNOWN", "date": None},
        FactKey.INCIDENT_DATE: {"precision": "EXACT", "date": "not-a-date"},
        FactKey.SERVICE_DATE: {"precision": "APPROXIMATE", "date": "2026-01-10"},
    }

    assert _analysis_date(facts) == date(2026, 1, 10)


def test_submission_contract_maps_to_domain_and_verified_actions() -> None:
    payload = _submission()
    claims = _domain_claims(payload)
    reviews = _semantic_reviews(payload)

    assert payload.expected_clinic_document_context_trace_sha256 == "c" * 64
    assert claims[0].kind is ClaimKind.ACTION
    assert claims[0].required_fact_keys == (FactKey.FORMAL_CLAIM,)
    assert reviews[0].verdict is SemanticVerdict.SUPPORTED
    assert _verified_action_items(
        claims,
        {"action-1": VerificationResult.VERIFIED},
    ) == ["Зафиксировать обращение пациента."]
    assert _verified_action_items(
        claims,
        {"action-1": VerificationResult.UNSUPPORTED},
    ) == []


def _report_response() -> ReportResponse:
    return ReportResponse(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        caseId=UUID("00000000-0000-0000-0000-000000000011"),
        reportVersion=1,
        reportJson={"schemaVersion": "dental-case-report.v1"},
        pdfSha256="d" * 64,
        createdAt=datetime(2026, 9, 4, tzinfo=UTC),
    )


def test_analysis_response_exposes_only_a_consistent_server_escalation_pointer() -> None:
    escalation_id = UUID("00000000-0000-0000-0000-000000000012")
    response = AnalysisSubmissionResponse(
        analysisAllowed=True,
        riskLevel="HIGH",
        escalationRequired=True,
        escalationId=escalation_id,
        report=_report_response(),
    )

    assert response.escalation_id == escalation_id

    with pytest.raises(ValueError, match="must include escalationId"):
        AnalysisSubmissionResponse(
            analysisAllowed=True,
            riskLevel="HIGH",
            escalationRequired=True,
            report=_report_response(),
        )
    with pytest.raises(ValueError, match="cannot include escalationId"):
        AnalysisSubmissionResponse(
            analysisAllowed=True,
            riskLevel="LOW",
            escalationRequired=False,
            escalationId=escalation_id,
            report=_report_response(),
        )
