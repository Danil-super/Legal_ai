"""Synthetic non-authoritative analogies; neither approval nor legal evidence."""

import hashlib
from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from legal_core.reference_analogy_contracts import (
    ReferenceAnalogyContext,
    ReferenceAnalogyExample,
    build_reference_analogy_context,
)


def example(index=1, *, text="Синтетическая ситуация: пациент просит проверить оплату."):
    return ReferenceAnalogyExample.model_validate({
        "source": {
            "referenceCaseId": UUID(int=index), "versionId": UUID(int=index + 10),
            "version": 1, "approvalEventId": UUID(int=index + 20), "groupKey": "healthcare",
            "evaluationAsOfDate": "2026-10-08",
            "scenarioSha256": hashlib.sha256(text.encode()).hexdigest(),
        }, "scenarioText": text,
    })


def test_complete_text_is_source_bound_and_explicitly_not_law_or_event_date():
    item = example()
    context = build_reference_analogy_context([item])
    wire = context.model_dump(mode="json", by_alias=True)
    assert wire["examples"][0]["purpose"] == "NON_AUTHORITATIVE_ANALOGY"
    assert wire["examples"][0]["dateBasis"] == "EVALUATION_DATE_NOT_EVENT_DATE"
    assert wire["examples"][0]["scenarioText"] == item.scenario_text
    assert "evidenceFragmentIds" not in str(wire)
    assert ReferenceAnalogyContext.model_validate(wire) == context


@pytest.mark.parametrize("fault", ["hash", "text", "purpose", "date", "raw", "actor"])
def test_tampered_text_authority_and_private_metadata_are_refused(fault):
    wire = example().model_dump(mode="json", by_alias=True)
    if fault == "hash":
        wire["source"]["scenarioSha256"] = "f" * 64
    elif fault == "text":
        wire["scenarioText"] += " Ещё условие."
    elif fault == "purpose":
        wire["purpose"] = "LEGAL_EVIDENCE"
    elif fault == "date":
        wire["dateBasis"] = "SERVICE_DATE"
    else:
        wire["source"]["rawObjectKey" if fault == "raw" else "reviewerMembershipId"] = "private"
    with pytest.raises(ValidationError):
        ReferenceAnalogyExample.model_validate(wire)


def test_trace_binds_exact_order_version_review_group_and_evaluation_date():
    context = build_reference_analogy_context([example(1), example(2)])
    reordered = build_reference_analogy_context([example(2), example(1)])
    assert context.trace_sha256 != reordered.trace_sha256
    for name, value in [("version", 2), ("groupKey", "general"),
                        ("approvalEventId", str(UUID(int=99))),
                        ("evaluationAsOfDate", "2026-10-09")]:
        wire = context.model_dump(mode="json", by_alias=True)
        wire["examples"][0]["source"][name] = value
        with pytest.raises(ValidationError):
            ReferenceAnalogyContext.model_validate(wire)


def test_bounded_context_never_silently_truncates_or_accepts_duplicate_sources():
    assert len(build_reference_analogy_context([example(text="я" * 12000)]).examples) == 1
    for items in [[example(x) for x in range(1, 5)], [example(), example()],
                  [example(1, text="я" * 6001), example(2, text="я" * 6000)]]:
        with pytest.raises(ValidationError):
            build_reference_analogy_context(items)
    empty = build_reference_analogy_context([])
    assert empty.examples == [] and len(empty.trace_sha256) == 64


def test_forged_nested_model_and_mutated_lists_are_revalidated_at_boundary():
    context = build_reference_analogy_context([example()])
    forged = deepcopy(context)
    forged.examples[0].__dict__["purpose"] = "LEGAL_EVIDENCE"
    with pytest.raises(ValidationError):
        ReferenceAnalogyContext.model_validate(forged)


def test_identifier_errors_hide_synthetic_input_and_existing_phone_is_not_allowed():
    private = "Синтетический контакт +7 999 123-45-67, нельзя отправлять модели."
    with pytest.raises(ValidationError) as caught:
        example(text=private)
    assert private not in str(caught.value)
