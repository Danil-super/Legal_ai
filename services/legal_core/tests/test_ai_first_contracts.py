"""New inactive contracts preserve source provenance, not model authority."""

import hashlib
from datetime import date
from uuid import UUID

import pytest
from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.intake_answer_contracts import (
    FactualContextProposal,
    MaterialQuestion,
    RetainedSourceSegment,
    derive_answer_question,
)
from pydantic import ValidationError

SOURCE = UUID("00000000-0000-0000-0000-000000000101")
TEXT = "15.10.2026 пригласили на осмотр; пациент просит вернуть 5000 рублей."


def segment(**change):
    return {"sourceId": str(SOURCE), "kind": "FOLLOWUP_ANSWER", "revision": 2,
            "text": TEXT, "sourceSha256": hashlib.sha256(TEXT.encode()).hexdigest(), **change}


def question(state):
    return derive_answer_question({"intakeVersion": 2, "situationAreas": ["SERVICE"]}, state)


def candidate(state, value, start, end):
    return {"question": question(state).model_dump(mode="json", by_alias=True), "value": value,
            "anchors": [{"sourceId": str(SOURCE), "sourceSha256": segment()["sourceSha256"],
                         "start": start, "end": end}]}


def proposal(**change):
    return {"schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
        candidate("EVENT_DATE", {"date": "2026-10-15", "precision": "EXACT"}, 0, 10),
        candidate("CLINIC_ACTIONS", ["INVITED_FOR_EXAMINATION"], 11, 31),
    ], **change}


def work_product(**change):
    return {"schemaVersion": "ai-first-work-product.v1", "kind": "LAWYER_REVIEW_DRAFT",
            "workProductId": "00000000-0000-0000-0000-000000000201", "version": 1,
            "parentVersion": None, "clinicId": "00000000-0000-0000-0000-000000000301",
            "source": {"operationId": "00000000-0000-0000-0000-000000000401",
                "caseId": "00000000-0000-0000-0000-000000000501",
                "factSnapshotSha256": "a" * 64, "context": None},
            "asOfDate": "2026-10-26", "riskPolicyVersion": "dental-risk.v4",
            "status": "UNAPPROVED", "summary": "Синтетическое обращение о возврате оплаты.",
            "actions": ["Подготовить имеющуюся переписку для проверки."],
            "uncertainties": ["Обстоятельства требуют проверки юристом."],
            "legalFragmentIds": [], **change}


def test_rich_reply_can_carry_two_distinct_grounded_facts_without_confirming_them():
    parsed = FactualContextProposal.model_validate(proposal())
    parsed.validate_sources([RetainedSourceSegment.model_validate(segment())])
    assert len(parsed.candidates) == 2
    assert parsed.model_dump(mode="json", by_alias=True) == proposal()


@pytest.mark.parametrize("change", [
    {"sourceSha256": "b" * 64}, {"text": " "}, {"revision": True},
    {"revision": 0}, {"kind": "LAWYER_APPROVAL"}, {"text": "mail@example.invalid"},
])
def test_retained_sources_reject_false_hashes_unbounded_identity_and_new_authority(change):
    with pytest.raises(ValidationError):
        RetainedSourceSegment.model_validate(segment(**change))


@pytest.mark.parametrize("fault", ["unknown-source", "wrong-hash", "outside", "constructed"])
def test_factual_anchors_cannot_be_rebound_to_other_text_or_forged_nested_instances(fault):
    wire = proposal()
    anchor = wire["candidates"][0]["anchors"][0]
    if fault == "unknown-source":
        anchor["sourceId"] = "00000000-0000-0000-0000-000000000999"
    elif fault == "wrong-hash":
        anchor["sourceSha256"] = "b" * 64
    elif fault == "outside":
        anchor["end"] = len(TEXT) + 1
    parsed = FactualContextProposal.model_validate(wire)
    if fault == "constructed":
        parsed.candidates[0].anchors[0].__dict__["start"] = True
    with pytest.raises(ValueError):
        parsed.validate_sources([RetainedSourceSegment.model_validate(segment())])


def test_duplicate_targets_and_source_ids_are_not_silently_merged():
    wire = proposal()
    wire["candidates"].append(wire["candidates"][0])
    with pytest.raises(ValidationError):
        FactualContextProposal.model_validate(wire)
    parsed = FactualContextProposal.model_validate(proposal())
    source = RetainedSourceSegment.model_validate(segment())
    with pytest.raises(ValueError):
        parsed.validate_sources([source, source])


@pytest.mark.parametrize("extra", [{"riskLevel": "LOW"}, {"confirmed": True},
                                    {"approveLegalVersions": []}])
def test_model_factual_proposal_cannot_change_risk_or_confirm_sources(extra):
    with pytest.raises(ValidationError):
        FactualContextProposal.model_validate(proposal(**extra))


def test_material_question_requires_a_bounded_decision_reason_not_model_routing():
    parsed = MaterialQuestion.model_validate({
        "question": question("EVENT_DATE"), "reasonCode": "SOURCE_APPLICABILITY",
    })
    assert parsed.reason_code == "SOURCE_APPLICABILITY"
    with pytest.raises(ValidationError):
        MaterialQuestion.model_validate({"question": question("EVENT_DATE"),
                                         "reasonCode": "COMPLETE_ALL_SEVEN_STEPS"})


def test_work_product_is_separate_unapproved_content_not_a_report_or_patient_send():
    parsed = InternalWorkProduct.model_validate(work_product())
    assert parsed.status == "UNAPPROVED" and parsed.source.context is None
    assert parsed.model_dump(mode="json", by_alias=True) == work_product()
    assert len(parsed.content_sha256) == 64 and parsed.as_of_date == date(2026, 10, 26)


@pytest.mark.parametrize("change", [
    {"status": "APPROVED"}, {"version": True}, {"version": 2}, {"parentVersion": 1},
    {"version": 3, "parentVersion": 1}, {"asOfDate": "2026-10-26T00:00:00Z"},
    {"reportId": "00000000-0000-0000-0000-000000000999"}, {"sendToPatient": True},
    {"actions": []}, {"summary": " "}, {"legalFragmentIds": [str(SOURCE)]},
])
def test_work_product_rejects_forged_approval_versions_and_unbound_legal_fragments(change):
    with pytest.raises(ValidationError):
        InternalWorkProduct.model_validate(work_product(**change))


def test_new_version_retains_parent_link_and_distinct_content_hash():
    first = InternalWorkProduct.model_validate(work_product())
    second = InternalWorkProduct.model_validate(work_product(version=2, parentVersion=1))
    assert second.version == 2 and second.content_sha256 != first.content_sha256


def test_rejected_candidate_does_not_echo_private_input_in_error_text():
    wire = proposal()
    wire["candidates"][0]["value"] = "private@example.invalid"
    with pytest.raises(ValidationError) as caught:
        FactualContextProposal.model_validate(wire)
    assert "private@example.invalid" not in str(caught.value)


def test_changed_source_text_cannot_reuse_previous_anchors():
    source = segment(text=TEXT.replace("5000", "9000"))
    source["sourceSha256"] = hashlib.sha256(source["text"].encode()).hexdigest()
    parsed = FactualContextProposal.model_validate(proposal())
    with pytest.raises(ValueError):
        parsed.validate_sources([RetainedSourceSegment.model_validate(source)])


@pytest.mark.parametrize("field", ["asOfDate", "riskPolicyVersion"])
def test_work_product_cannot_change_bound_context(field):
    wire = work_product()
    wire["source"]["context"] = {
        "asOfDate": "2026-10-26", "riskPolicyVersion": "dental-risk.v4",
        "evidenceTraceSha256": "b" * 64, "clinicDocumentContextTraceSha256": "c" * 64,
    }
    wire[field] = "2026-10-27" if field == "asOfDate" else "dental-risk.v1"
    with pytest.raises(ValidationError):
        InternalWorkProduct.model_validate(wire)


def test_nested_mutation_is_revalidated_before_work_product_hashing():
    parsed = InternalWorkProduct.model_validate(work_product())
    parsed.actions.append("private@example.invalid")
    with pytest.raises(ValidationError):
        _ = parsed.content_sha256
