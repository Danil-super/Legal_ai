"""Unapproved work products cannot carry model-authored authority or patient commands."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.lawyer_work_product_contracts import (
    LawyerWorkProductReview,
    LawyerWorkProductSubmission,
    LawyerWorkProductView,
)
from test_ai_first_contracts import work_product


def submission():
    source = work_product()["source"]
    source["context"] = {
        "asOfDate": "2026-10-26", "riskPolicyVersion": "dental-risk.v4",
        "evidenceTraceSha256": "b" * 64, "clinicDocumentContextTraceSha256": "c" * 64,
    }
    fragment = str(uuid4())
    return {"expectedSource": source, "claims": [{
        "claimId": "organize-records", "kind": "ACTION",
        "text": "Подготовить имеющуюся переписку для проверки.",
        "evidenceFragmentIds": [fragment], "requiredFactKeys": ["EVENT_SUMMARY"],
    }], "semanticReviews": [{
        "claimId": "organize-records", "verdict": "SUPPORTED", "reviewedFragmentIds": [fragment],
    }]}


def test_source_bound_submission_contains_no_generated_approval_or_report_identity():
    wire = submission()
    parsed = LawyerWorkProductSubmission.model_validate(wire)
    assert parsed.model_dump(mode="json", by_alias=True) == wire


@pytest.mark.parametrize("extra", [{"reviewState": "HUMAN_APPROVED"}, {"riskLevel": "LOW"},
                                   {"sendToPatient": True}, {"reportId": str(uuid4())},
                                   {"workProductId": str(uuid4())}, {"version": 9}])
def test_generator_does_not_choose_storage_identity_status_or_delivery(extra):
    with pytest.raises(ValidationError):
        LawyerWorkProductSubmission.model_validate(submission() | extra)


@pytest.mark.parametrize("fault", ["no-context", "duplicate-claim", "unknown-review",
                                   "duplicate-review", "too-many-actions", "long-action", "pii"])
def test_missing_bindings_or_invalid_work_product_claims_are_rejected_without_echo(fault):
    wire = submission()
    if fault == "no-context":
        wire["expectedSource"]["context"] = None
    elif fault == "duplicate-claim":
        wire["claims"].append(wire["claims"][0])
    elif fault == "unknown-review":
        wire["semanticReviews"][0]["claimId"] = "unknown"
    elif fault == "duplicate-review":
        wire["semanticReviews"].append(wire["semanticReviews"][0])
    elif fault == "too-many-actions":
        wire["claims"] = [dict(wire["claims"][0], claimId=f"step-{index}") for index in range(9)]
        wire["semanticReviews"] = []
    elif fault == "long-action":
        wire["claims"][0]["text"] = "x" * 601
    else:
        wire["claims"][0]["text"] = "private@example.invalid"
    original = deepcopy(wire)
    with pytest.raises(ValidationError) as caught:
        LawyerWorkProductSubmission.model_validate(wire)
    assert "private@example.invalid" not in str(caught.value)
    assert wire == original


def test_view_binds_exact_immutable_content_and_distinguishes_human_review_from_payload_status():
    content = InternalWorkProduct.model_validate(work_product())
    wire = {"versionId": str(uuid4()), "reviewState": "UNAPPROVED", "origin": "AI",
            "isCurrent": False, "contentSha256": content.content_sha256,
            "content": content.model_dump(mode="json", by_alias=True), "citations": []}
    assert LawyerWorkProductView.model_validate(wire).model_dump(mode="json", by_alias=True) == wire
    with pytest.raises(ValidationError):
        LawyerWorkProductView.model_validate(wire | {"contentSha256": "b" * 64})


@pytest.mark.parametrize("decision,changes", [
    ("APPROVE", {"actions": ["Несверенное изменение."]}),
    ("APPROVE", {"summary": "Несверенное изменение."}),
    ("REVISE", {}), ("REVISE", {"actions": []}), ("REVISE", {"actions": [" "]}),
    ("REVISE", {"actions": ["private@example.invalid"]}),
])
def test_review_is_explicit_and_cannot_approve_unseen_changes_or_invalid_text(decision, changes):
    with pytest.raises(ValidationError):
        LawyerWorkProductReview.model_validate({
            "expectedVersionId": str(uuid4()), "expectedContentSha256": "a" * 64,
            "decision": decision, **changes,
        })
