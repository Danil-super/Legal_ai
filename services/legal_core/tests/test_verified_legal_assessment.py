"""Verified legal reasoning survives storage and remains explicitly reviewable."""

import hashlib
import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.lawyer_work_product_contracts import LawyerWorkProductReview
from test_ai_first_contracts import work_product


ASSESSMENT = "Требование о возврате следует оценивать с учётом установленных обстоятельств."


def test_legal_assessment_is_retained_and_changes_the_immutable_content_hash():
    old = InternalWorkProduct.model_validate(work_product())
    new = InternalWorkProduct.model_validate(work_product(legalAssessment=[ASSESSMENT]))
    assert new.legal_assessment == [ASSESSMENT]
    assert new.model_dump(mode="json", by_alias=True)["legalAssessment"] == [ASSESSMENT]
    assert new.content_sha256 != old.content_sha256


def test_absent_or_empty_assessment_preserves_historical_json_and_content_hash():
    wire = work_product()
    historical_hash = hashlib.sha256(json.dumps(
        wire, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    for payload in (wire, wire | {"legalAssessment": []}):
        parsed = InternalWorkProduct.model_validate(payload)
        assert parsed.legal_assessment == []
        assert parsed.model_dump(mode="json", by_alias=True) == wire
        assert parsed.content_sha256 == historical_hash


@pytest.mark.parametrize("assessment", [[" "], ["private@example.invalid"],
                                         ["x" * 601], ["Проверить обстоятельства."] * 9])
def test_assessment_rejects_identifiers_blank_or_unbounded_text(assessment):
    with pytest.raises(ValidationError) as caught:
        InternalWorkProduct.model_validate(work_product(legalAssessment=assessment))
    assert "private@example.invalid" not in str(caught.value)


def test_lawyer_can_explicitly_revise_or_clear_assessment_but_not_approve_unseen_changes():
    binding = {"expectedVersionId": UUID(int=1), "expectedContentSha256": "a" * 64}
    for value in ([ASSESSMENT], []):
        review = LawyerWorkProductReview.model_validate({
            **binding, "decision": "REVISE", "legalAssessment": value,
        })
        assert review.legal_assessment == value
        with pytest.raises(ValidationError):
            LawyerWorkProductReview.model_validate({
                **binding, "decision": "APPROVE", "legalAssessment": value,
            })


def test_review_rejects_identifying_legal_assessment():
    with pytest.raises(ValidationError) as caught:
        LawyerWorkProductReview.model_validate({
            "expectedVersionId": UUID(int=1), "expectedContentSha256": "a" * 64,
            "decision": "REVISE", "legalAssessment": ["private@example.invalid"],
        })
    assert "private@example.invalid" not in str(caught.value)
