from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from legal_core.analysis_api import (
    _analysis_date,
    _domain_claims,
    _require_analysis_eligible_case,
    _semantic_reviews,
    _verified_action_items,
)
from legal_core.analysis_contracts import AnalysisSubmissionRequest, AnalysisSubmissionResponse
from legal_core.api_contracts import ReportResponse
from legal_core.case_api import ApiError
from legal_core.contracts import CaseStatus, FactKey
from legal_core.verifier import ClaimKind, SemanticVerdict, VerificationResult
from legal_core.analysis import analyze_frozen_case
from legal_core.risk_engine import RiskLevel, RiskPolicy


FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_abstention_contract_reaches_blocked_domain_outcome() -> None:
    data = _submission().model_dump(by_alias=True)
    data.update(claims=[], semanticReviews=[])
    payload = AnalysisSubmissionRequest.model_validate(data)
    outcome = analyze_frozen_case(
        facts={}, as_of_date=payload.as_of_date, evidence=[],
        claims=_domain_claims(payload), semantic_reviews=_semantic_reviews(payload),
        risk_policy=RiskPolicy("risk.v1", 100_000),
    )
    assert not outcome.analysis_allowed
    assert outcome.risk.level is RiskLevel.UNAVAILABLE


def test_submission_rejects_review_for_another_claim_at_contract_boundary() -> None:
    data = _submission().model_dump(by_alias=True)
    data["semanticReviews"][0]["claimId"] = "unknown-claim"
    with pytest.raises(ValueError, match="unknown claim identifiers"):
        AnalysisSubmissionRequest.model_validate(data)


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
        FactKey.SERVICE_DATE: {"precision": "EXACT", "date": "2026-01-10"},
    }

    assert _analysis_date(facts) == date(2026, 1, 10)


@pytest.mark.parametrize(
    "facts",
    [
        {},
        {key: {"precision": "UNKNOWN", "date": None} for key in (
            FactKey.CLAIM_DATE, FactKey.INCIDENT_DATE, FactKey.SERVICE_DATE
        )},
        {FactKey.CLAIM_DATE: {"precision": "EXACT", "date": "not-a-date"}},
    ],
)
def test_analysis_date_never_substitutes_today_for_unknown_dates(facts) -> None:
    with pytest.raises(ApiError) as raised:
        _analysis_date(facts)
    assert raised.value.status_code == 422
    assert raised.value.code == "ANALYSIS_DATE_UNCERTAIN"


@pytest.mark.parametrize("approximate_date", ["2026-08-31", "2026-09-01"])
def test_approximate_primary_date_cannot_select_a_legal_revision(approximate_date: str) -> None:
    with pytest.raises(ApiError) as raised:
        _analysis_date({
            FactKey.CLAIM_DATE: {"precision": "APPROXIMATE", "date": approximate_date},
            FactKey.INCIDENT_DATE: {"precision": "EXACT", "date": "2026-08-20"},
        })
    assert raised.value.code == "ANALYSIS_DATE_UNCERTAIN"
    assert raised.value.details["factKey"] == "CLAIM_DATE"


def test_exact_primary_date_does_not_require_lower_priority_dates() -> None:
    assert _analysis_date({
        FactKey.CLAIM_DATE: {"precision": "EXACT", "date": "2026-09-01"},
        FactKey.INCIDENT_DATE: {"precision": "APPROXIMATE", "date": "2026-08-31"},
        FactKey.SERVICE_DATE: {"precision": "UNKNOWN", "date": None},
    }) == date(2026, 9, 1)


def test_guided_v2_analysis_uses_exact_event_date_not_legacy_fields() -> None:
    facts = {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.EVENT_DATE: {"precision": "EXACT", "date": "2026-09-01"},
    }

    assert _analysis_date(facts) == date(2026, 9, 1)


def test_guided_v2_never_falls_back_to_legacy_date_when_event_is_approximate() -> None:
    facts = {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.EVENT_DATE: {"precision": "APPROXIMATE", "date": "2026-09-01"},
        FactKey.CLAIM_DATE: {"precision": "EXACT", "date": "2026-09-02"},
    }

    with pytest.raises(ApiError) as raised:
        _analysis_date(facts)
    assert raised.value.code == "ANALYSIS_DATE_UNCERTAIN"
    assert raised.value.details["factKey"] == "EVENT_DATE"


def test_guided_v2_unknown_event_date_reports_only_the_guided_date() -> None:
    facts = {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.EVENT_DATE: {"precision": "UNKNOWN", "date": None},
        FactKey.CLAIM_DATE: {"precision": "EXACT", "date": "2026-09-02"},
    }

    with pytest.raises(ApiError) as raised:
        _analysis_date(facts)
    assert raised.value.code == "ANALYSIS_DATE_UNCERTAIN"
    assert raised.value.details["missingFactKeys"] == ["EVENT_DATE"]


def test_completed_case_is_rejected_before_agent_reasoning_can_start() -> None:
    case = SimpleNamespace(
        closed_at=datetime(2026, 9, 8, tzinfo=UTC),
        status=CaseStatus.REPORT_READY.value,
    )

    with pytest.raises(ApiError) as raised:
        _require_analysis_eligible_case(case)  # type: ignore[arg-type]

    assert raised.value.status_code == 409
    assert raised.value.code == "CASE_ANALYSIS_ALREADY_COMPLETED"


def test_expired_case_cannot_start_or_commit_analysis_before_retention_purge() -> None:
    case = SimpleNamespace(
        closed_at=datetime(2026, 1, 1, tzinfo=UTC),
        retention_due_at=datetime(2026, 1, 2, tzinfo=UTC),
        status=CaseStatus.ANALYSIS_BLOCKED.value,
    )
    with pytest.raises(ApiError) as raised:
        _require_analysis_eligible_case(case)  # type: ignore[arg-type]
    assert raised.value.status_code == 410


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
