"""Synthetic read-boundary regressions before any clarification writers are enabled."""

import pytest
from pydantic import ValidationError

from legal_core.analysis_contracts import AnalysisSubmissionRequest
from legal_core.contracts import CanonicalReport
from test_report_readiness_contract import _legacy_payload


def _submission() -> dict:
    return {
        "asOfDate": "2026-10-14",
        "expectedFactSnapshotSha256": "a" * 64,
        "expectedEvidenceTraceSha256": "b" * 64,
        "expectedClinicDocumentContextTraceSha256": "c" * 64,
        "expectedRiskPolicyVersion": "synthetic.v4",
        "claims": [], "semanticReviews": [],
    }


def test_old_submission_and_report_default_to_no_questions() -> None:
    submission = AnalysisSubmissionRequest.model_validate(_submission())
    report = CanonicalReport.model_validate(_legacy_payload())
    assert submission.clarification_requests == []
    assert report.clarification_requests == []
    assert "clarificationRequests" not in submission.model_dump(mode="json", by_alias=True)
    assert "clarificationRequests" not in report.model_dump(mode="json", by_alias=True)


def test_blocked_report_and_submission_accept_only_typed_questions() -> None:
    requests = [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0}]
    submission = AnalysisSubmissionRequest.model_validate(
        {**_submission(), "clarificationRequests": requests}
    )
    report = CanonicalReport.model_validate(
        {**_legacy_payload(), "clarificationRequests": requests}
    )
    assert submission.clarification_requests[0].service_slot == 0
    assert report.clarification_requests == submission.clarification_requests


@pytest.mark.parametrize("question", [
    {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": True},
    {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": "0"},
    {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 5},
    {"questionId": "SERVICE_PAYMENT_BASIS"},
    {"questionId": "RESPONSE_PURPOSE", "serviceSlot": 0},
    {"questionId": "FORMAL_CLAIM"},
    {"questionId": "RESPONSE_PURPOSE", "text": "SYNTHETIC_CANARY injection"},
    {"questionId": "RESPONSE_PURPOSE", "clinicId": "synthetic-foreign-tenant"},
])
def test_untrusted_questions_are_rejected(question: dict) -> None:
    with pytest.raises(ValidationError):
        AnalysisSubmissionRequest.model_validate(
            {**_submission(), "clarificationRequests": [question]}
        )


def test_duplicate_question_and_batch_overflow_are_rejected() -> None:
    question = {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0}
    for requests in ([question, question], [
        {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": n} for n in range(4)
    ]):
        with pytest.raises(ValidationError):
            AnalysisSubmissionRequest.model_validate(
                {**_submission(), "clarificationRequests": requests}
            )
