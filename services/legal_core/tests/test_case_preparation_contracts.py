"""Pure contract tests: these do not establish runtime authz or legal quality."""

from copy import deepcopy
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from legal_core.analysis_contracts import (
    AnalysisSubmissionResponse,
    PreparationResult,
    ReportReadyResult,
)
from legal_core.api_contracts import ReportResponse
from legal_core.case_preparation_contracts import (
    HumanReviewResult,
    LimitedResult,
    NeedsFactsResult,
    PreparationSource,
)
from legal_core.contracts import CaseStatus, FactKey
from legal_core.reports import build_analysis_report, build_intake_report
from legal_core.risk_engine import RiskAssessment, RiskLevel
from test_reports import _evidence, _verification


OPERATION_ID = UUID("00000000-0000-0000-0000-000000000001")
CASE_ID = UUID("00000000-0000-0000-0000-000000000020")
REPORT_ID = UUID("00000000-0000-0000-0000-000000000010")
OTHER_ID = UUID("00000000-0000-0000-0000-000000000099")
GENERATED_AT = datetime(2026, 8, 31, 10, tzinfo=UTC)


def _source() -> dict:
    return {
        "operationId": str(OPERATION_ID),
        "caseId": str(CASE_ID),
        "factSnapshotSha256": "a" * 64,
        "context": None,
    }


def _needs_facts() -> dict:
    return {
        "resultVersion": 2,
        "status": "NEEDS_FACTS",
        "source": _source(),
        "reasonCode": "FACTUAL_CLARIFICATION_REQUIRED",
        "clarificationRequests": [{"questionId": "RESPONSE_PURPOSE"}],
        "missingFactKeys": [],
    }


def _limited() -> dict:
    return {
        "resultVersion": 2,
        "status": "LIMITED",
        "source": _source(),
        "reasonCode": "ANALYSIS_DATE_UNCERTAIN",
    }


def _human_review() -> dict:
    return {
        "resultVersion": 2,
        "status": "HUMAN_REVIEW",
        "source": _source(),
        "escalationId": str(OTHER_ID),
        "riskLevel": "HIGH",
        "riskPolicyVersion": "dental-risk.v1",
    }


def _ready() -> dict:
    canonical = build_analysis_report(
        report_id=REPORT_ID,
        analysis_run_id=OPERATION_ID,
        case_id=CASE_ID,
        public_number="DL-SYNTHETIC-001",
        case_status=CaseStatus.REPORT_READY,
        report_version=2,
        generated_at=GENERATED_AT,
        as_of_date=date(2026, 8, 31),
        facts={FactKey.PROBLEM_SUMMARY: "Синтетический обезличенный пример."},
        missing_facts=[],
        risk=RiskAssessment(
            level=RiskLevel.LOW,
            reason_codes=("NO_ESCALATION_TRIGGER",),
            policy_version="dental-risk.v1",
            fact_snapshot_sha256="a" * 64,
            external_draft_allowed=True,
        ),
        evidence_trace_sha256="b" * 64,
        evidence=[_evidence()],
        verification=_verification(),
        clinic_document_context_trace_sha256="c" * 64,
        clinic_document_context=[],
        verified_action_items=["Зафиксировать синтетическое обращение."],
    )
    report = ReportResponse(
        id=REPORT_ID,
        caseId=CASE_ID,
        reportVersion=2,
        reportJson=canonical.model_dump(mode="json", by_alias=True),
        pdfSha256="d" * 64,
        createdAt=GENERATED_AT,
    )
    source = _source()
    source["factSnapshotSha256"] = canonical.fact_snapshot_sha256
    source["context"] = {
        "asOfDate": "2026-08-31",
        "evidenceTraceSha256": "b" * 64,
        "clinicDocumentContextTraceSha256": "c" * 64,
        "riskPolicyVersion": "dental-risk.v1",
    }
    return {
        "resultVersion": 2,
        "status": "READY",
        "source": source,
        "analysisAllowed": True,
        "report": report.model_dump(mode="json", by_alias=True),
    }


@pytest.mark.parametrize(
    ("factory", "variant"),
    [
        (_needs_facts, NeedsFactsResult),
        (_limited, LimitedResult),
        (_human_review, HumanReviewResult),
        (_ready, ReportReadyResult),
    ],
)
def test_result_union_selects_variant_and_serializes_without_root_wrapper(factory, variant) -> None:
    payload = factory()
    result = PreparationResult.model_validate(payload)
    assert isinstance(result.root, variant)
    serialized = result.model_dump(mode="json", by_alias=True)
    assert serialized["resultVersion"] == 2
    assert serialized["status"] == payload["status"]
    assert serialized["source"] == payload["source"]
    assert "root" not in serialized
    assert PreparationResult.model_validate(serialized) == result


def test_ready_uses_typed_report_response_and_available_verified_canonical_report() -> None:
    result = PreparationResult.model_validate(_ready()).root
    assert isinstance(result, ReportReadyResult)
    assert isinstance(result.report, ReportResponse)
    canonical = result.report.report_json
    assert canonical["summary"]["analysisAvailability"]["status"] == "READY"
    assert canonical["analysis"]["verifierStatus"] == "PASSED"
    assert canonical["recommendations"]["status"] == "AVAILABLE"
    assert canonical["legalBasis"]["status"] == "AVAILABLE"


@pytest.mark.parametrize("factory", [_needs_facts, _limited, _human_review, _ready])
@pytest.mark.parametrize("version", [None, 1, 3, True, 2.0, "2"])
def test_result_version_is_mandatory_exact_integer_two(factory, version) -> None:
    payload = factory()
    if version is None:
        del payload["resultVersion"]
    else:
        payload["resultVersion"] = version
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


@pytest.mark.parametrize("status", [None, "REPORT_READY", "UNKNOWN", 2])
def test_union_requires_known_status_discriminator(status) -> None:
    payload = _limited()
    if status is None:
        del payload["status"]
    else:
        payload["status"] = status
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


@pytest.mark.parametrize("field", ["operationId", "caseId", "factSnapshotSha256", "context"])
def test_source_identity_and_explicit_context_are_mandatory(field: str) -> None:
    source = _source()
    del source[field]
    with pytest.raises(ValidationError):
        PreparationSource.model_validate(source)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operationId", "not-a-uuid"),
        ("caseId", "not-a-uuid"),
        ("factSnapshotSha256", "A" * 64),
        ("factSnapshotSha256", "a" * 63),
        ("factSnapshotSha256", "g" * 64),
    ],
)
def test_source_rejects_malformed_identities_and_fingerprint(field, value) -> None:
    source = _source()
    source[field] = value
    with pytest.raises(ValidationError):
        PreparationSource.model_validate(source)


@pytest.mark.parametrize("factory", [_needs_facts, _limited, _human_review, _ready])
@pytest.mark.parametrize("scope", ["result", "source", "context"])
@pytest.mark.parametrize("extra", ["clinicId", "actorId", "tenantId"])
def test_public_result_rejects_tenant_or_actor_in_every_envelope_scope(
    factory, scope, extra,
) -> None:
    payload = factory()
    if scope == "context":
        payload["source"]["context"] = deepcopy(_ready()["source"]["context"])
        target = payload["source"]["context"]
    else:
        target = payload if scope == "result" else payload["source"]
    target[extra] = str(OTHER_ID)
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ["asOfDate", "evidenceTraceSha256", "clinicDocumentContextTraceSha256", "riskPolicyVersion"],
)
def test_source_context_cannot_be_partial(field) -> None:
    source = _ready()["source"]
    del source["context"][field]
    with pytest.raises(ValidationError):
        PreparationSource.model_validate(source)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("asOfDate", "2026-02-30"),
        ("evidenceTraceSha256", "B" * 64),
        ("clinicDocumentContextTraceSha256", "invalid"),
        ("riskPolicyVersion", ""),
        ("riskPolicyVersion", "   "),
        ("riskPolicyVersion", "v" * 81),
    ],
)
def test_context_rejects_invalid_date_hashes_or_policy(field, value) -> None:
    source = _ready()["source"]
    source["context"][field] = value
    with pytest.raises(ValidationError):
        PreparationSource.model_validate(source)


@pytest.mark.parametrize("factory", [_needs_facts, _limited, _human_review])
@pytest.mark.parametrize(
    "field",
    ["report", "recommendations", "legalConclusions", "legalText", "draftResponse", "advice"],
)
def test_nonready_results_cannot_carry_report_advice_or_patient_draft(factory, field) -> None:
    payload = factory()
    payload[field] = {"text": "Синтетический запрещённый вывод."}
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


def test_needs_facts_accepts_missing_facts_without_questions() -> None:
    payload = _needs_facts()
    payload.update(clarificationRequests=[], missingFactKeys=["EVENT_DATE"])
    assert PreparationResult.model_validate(payload).root.status == "NEEDS_FACTS"


@pytest.mark.parametrize(
    ("questions", "missing"),
    [
        ([], []),
        ([{"questionId": "RESPONSE_PURPOSE"}] * 2, []),
        ([], ["EVENT_DATE"] * 2),
        ([{"questionId": "RESPONSE_PURPOSE"}], ["EVENT_DATE", "EVENT_SUMMARY", "FORMAL_CLAIM"]),
        ([], ["EVENT_DATE", "EVENT_SUMMARY", "FORMAL_CLAIM", "HARM_CLAIMED"]),
        ([{"questionId": "FREE_TEXT", "text": "Произвольный вопрос"}], []),
        ([{"questionId": "SERVICE_PAYMENT_BASIS"}], []),
        ([{"questionId": "RESPONSE_PURPOSE", "serviceSlot": 0}], []),
        ([{"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": True}], []),
        ([{"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 5}], []),
        ([{"questionId": "RESPONSE_PURPOSE", "text": "extra"}], []),
        ([], ["NOT_A_FACT"]),
    ],
)
def test_needs_facts_rejects_empty_oversized_duplicate_or_invalid_question_scope(
    questions, missing,
) -> None:
    payload = _needs_facts()
    payload.update(clarificationRequests=questions, missingFactKeys=missing)
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


def test_needs_facts_accepts_three_distinct_closed_requests() -> None:
    payload = _needs_facts()
    payload["clarificationRequests"] = [
        {"questionId": "RESPONSE_PURPOSE"},
        {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0},
        {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 1},
    ]
    assert len(PreparationResult.model_validate(payload).root.clarification_requests) == 3


@pytest.mark.parametrize(
    "reason",
    [
        "INSUFFICIENT_FACTS", "ANALYSIS_DATE_UNCERTAIN", "LEGAL_EVIDENCE_UNAVAILABLE",
        "EVIDENCE_NOT_VERIFIED", "FACTUAL_SAFETY_UNCERTAIN",
    ],
)
def test_limited_reason_codes_are_closed_and_allow_known_abstentions(reason) -> None:
    payload = _limited()
    payload["reasonCode"] = reason
    assert PreparationResult.model_validate(payload).root.reason_code == reason


@pytest.mark.parametrize(
    "patch",
    [
        {"reasonCode": "FREE_FORM_REASON"},
        {"clarificationRequests": []},
        {"missingFactKeys": ["EVENT_DATE", "EVENT_DATE"]},
        {"missingFactKeys": list(FactKey)[:21]},
        {"missingFactKeys": ["NOT_A_FACT"]},
    ],
)
def test_limited_rejects_unknown_reason_questions_and_invalid_missing_facts(patch) -> None:
    payload = _limited()
    payload.update(patch)
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


@pytest.mark.parametrize("risk", ["HIGH", "CRITICAL"])
def test_human_review_requires_a_concrete_escalation_and_high_risk(risk) -> None:
    payload = _human_review()
    payload["riskLevel"] = risk
    assert PreparationResult.model_validate(payload).root.escalation_id == OTHER_ID


@pytest.mark.parametrize(
    "patch",
    [
        {"riskLevel": "LOW"}, {"riskLevel": "MEDIUM"}, {"riskLevel": "UNAVAILABLE"},
        {"escalationId": None}, {"escalationId": "invalid"},
        {"riskPolicyVersion": ""}, {"riskPolicyVersion": " "},
        {"riskPolicyVersion": "v" * 81},
    ],
)
def test_human_review_rejects_missing_escalation_low_risk_and_unbounded_policy(patch) -> None:
    payload = _human_review()
    payload.update(patch)
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


@pytest.mark.parametrize("value", [False, 0, 1, "true", "True", None])
def test_ready_analysis_allowed_requires_actual_true_boolean(value) -> None:
    payload = _ready()
    payload["analysisAllowed"] = value
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


def _replace(payload: dict, path: tuple[str | int, ...], value) -> None:
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source", "context"), None),
        (("source", "caseId"), str(OTHER_ID)),
        (("source", "factSnapshotSha256"), "f" * 64),
        (("source", "context", "asOfDate"), "2026-08-30"),
        (("source", "context", "evidenceTraceSha256"), "e" * 64),
        (("source", "context", "clinicDocumentContextTraceSha256"), "e" * 64),
        (("source", "context", "riskPolicyVersion"), "another-policy.v1"),
        (("report", "id"), str(OTHER_ID)),
        (("report", "caseId"), str(OTHER_ID)),
        (("report", "reportVersion"), 3),
        (("report", "reportJson", "case", "id"), str(OTHER_ID)),
        (("report", "reportJson", "reportId"), str(OTHER_ID)),
        (("report", "reportJson", "reportVersion"), 3),
        (("report", "reportJson", "factSnapshotSha256"), "e" * 64),
        (("report", "reportJson", "analysis", "asOfDate"), "2026-08-30"),
        (("report", "reportJson", "analysis", "evidenceTraceSha256"), "e" * 64),
        (("report", "reportJson", "analysis", "clinicDocumentContextTraceSha256"), "e" * 64),
        (("report", "reportJson", "risk", "policyVersion"), "another-policy.v1"),
        (("report", "reportJson", "analysis", "verifierStatus"), "FAILED"),
        (("report", "reportJson", "analysis"), None),
        (("report", "reportJson", "risk"), None),
        (("report", "reportJson", "risk", "escalationRequired"), True),
        (("report", "reportJson", "risk", "level"), "HIGH"),
        (("report", "reportJson", "risk", "level"), "UNAVAILABLE"),
        (("report", "reportJson", "clarificationRequests"), [{"questionId": "RESPONSE_PURPOSE"}]),
        (("report", "reportJson", "legalBasis"), {"status": "NOT_AVAILABLE", "sources": []}),
        (("report", "reportJson", "recommendations"), {"status": "NOT_AVAILABLE", "items": []}),
        (("report", "reportJson", "unexpected"), "extra"),
        (("report", "unexpected"), "extra"),
    ],
)
def test_ready_rejects_unbound_unverified_escalated_or_incomplete_reports(path, value) -> None:
    payload = _ready()
    _replace(payload, path, value)
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


def test_blocked_canonical_report_cannot_be_relabelled_ready_by_envelope() -> None:
    payload = _ready()
    blocked = build_intake_report(
        report_id=REPORT_ID, case_id=CASE_ID, public_number="DL-SYNTHETIC-001",
        case_status=CaseStatus.ANALYSIS_BLOCKED, report_version=2,
        generated_at=GENERATED_AT, facts={}, missing_facts=[],
    )
    payload["report"]["reportJson"] = blocked.model_dump(mode="json", by_alias=True)
    payload["source"]["factSnapshotSha256"] = blocked.fact_snapshot_sha256
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


def test_legacy_submission_response_still_requires_nonnull_report() -> None:
    with pytest.raises(ValidationError):
        AnalysisSubmissionResponse.model_validate({
            "analysisAllowed": False,
            "riskLevel": "UNAVAILABLE",
            "escalationRequired": False,
            "report": None,
        })


@pytest.mark.parametrize(("path", "value"), [
    (("source", "operationId"), str(OTHER_ID)),
    (("report", "reportJson", "facts", "PROBLEM_SUMMARY"), "Изменённый синтетический факт."),
    (("report", "reportJson", "legalBasis", "sources", 0, "effectiveFrom"), "2026-09-01"),
    (("report", "reportJson", "legalBasis", "sources", 0, "effectiveTo"), "2026-08-31"),
])
def test_ready_rejects_different_run_tampered_facts_and_inapplicable_sources(path, value) -> None:
    payload = _ready()
    _replace(payload, path, value)
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)


def test_constructed_report_instance_cannot_bypass_nested_validation() -> None:
    payload = _ready()
    report = ReportResponse.model_validate(payload["report"])
    report.report_json["analysis"]["verifierStatus"] = "BLOCKED"
    payload["report"] = report
    with pytest.raises(ValidationError):
        ReportReadyResult.model_validate(payload)


def test_human_review_cannot_mix_risk_policy_versions() -> None:
    payload = _human_review()
    payload["source"]["context"] = _ready()["source"]["context"]
    payload["riskPolicyVersion"] = "different-policy.v4"
    with pytest.raises(ValidationError):
        PreparationResult.model_validate(payload)
