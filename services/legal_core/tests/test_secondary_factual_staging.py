"""Partial answers stage supported secondary facts without fabricating a primary answer."""

from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest

from legal_core.intake_answer_contracts import AnswerInterpretation, derive_answer_question
from legal_core.intake_factual_candidates import (
    allowed_factual_questions,
    apply_factual_answer,
    retained_answer_sources,
)
from test_ai_first_candidates import candidate
from test_intake_clarification_preflight import complete_treatment_input

AS_OF = date(2026, 10, 28)
ANSWER = "Услуга оказана 01.10.2026."
DATE = {"date": "2026-10-01", "precision": "EXACT"}
PURPOSE_UNKNOWN = {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "UNKNOWN"}


def partial_bundle(*, operation=None, revision=7, data=None, answer=ANSWER):
    data = deepcopy(data if data is not None else complete_treatment_input())
    data.setdefault("analysisClarificationAnswers", {"answers": [PURPOSE_UNKNOWN]})
    current = derive_answer_question(data, "ANALYSIS_CLARIFICATION")
    sources = retained_answer_sources(operation or uuid4(), revision, data, answer)
    allowed = allowed_factual_questions(data, current)
    target = next(item for item in allowed if item.clarification_request is not None
                  and item.clarification_request.question_id == "SERVICE_PROVIDED_DATE")
    raw = {"status": "UNRESOLVED", "value": None, "sourceSpans": [],
           "reasonCode": "OFF_QUESTION", "factualProposal": {
               "schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
                   candidate(target, sources[-1], DATE, answer),
               ],
           }}
    return data, current, sources, allowed, raw


def test_secondary_date_survives_while_primary_payment_remains_unanswered():
    data, current, sources, _allowed, raw = partial_bundle()
    original = deepcopy(data)
    interpretation = AnswerInterpretation.model_validate(raw)
    result = apply_factual_answer(data, current, interpretation, ANSWER, sources, as_of_date=AS_OF)
    assert result["analysisClarificationAnswers"]["answers"] == [
        PURPOSE_UNKNOWN, {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0, "value": DATE},
    ]
    assert interpretation.value is None and interpretation.source_spans == []
    assert derive_answer_question(result, "ANALYSIS_CLARIFICATION") == current
    assert data == original


@pytest.mark.parametrize("invalid", ["primary-target", "foreign-service", "wrong-source",
                                   "overwrite-unknown", "missing-sources", "third-source",
                                   "wrong-scope"])
def test_partial_proposal_cannot_override_primary_scope_source_or_previous_attempt(invalid):
    data, current, sources, _allowed, raw = partial_bundle()
    addition = raw["factualProposal"]["candidates"][0]
    if invalid == "primary-target":
        addition.update(question=current.model_dump(mode="json", by_alias=True), value="PAID")
    elif invalid == "foreign-service":
        addition["question"]["clarificationRequest"]["serviceSlot"] = 1
    elif invalid == "wrong-source":
        addition["anchors"][0]["sourceId"] = str(uuid4())
    elif invalid == "overwrite-unknown":
        data["analysisClarificationAnswers"]["answers"].append({
            "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
            "value": {"date": None, "precision": "UNKNOWN"},
        })
    elif invalid == "missing-sources":
        sources = []
    elif invalid == "third-source":
        sources = [*sources, sources[-1].model_copy(update={"source_id": uuid4()})]
    else:
        addition["question"]["serviceScopeSha256"] = "0" * 64
    original = deepcopy(data)
    with pytest.raises(ValueError):
        apply_factual_answer(data, current, AnswerInterpretation.model_validate(raw), ANSWER,
                             sources, as_of_date=AS_OF)
    assert data == original


def test_unresolved_without_secondary_facts_cannot_stage_anything():
    data, current, sources, _allowed, raw = partial_bundle()
    raw.pop("factualProposal")
    with pytest.raises(ValueError):
        apply_factual_answer(data, current, AnswerInterpretation.model_validate(raw), ANSWER,
                             sources, as_of_date=AS_OF)


def test_unresolved_primary_never_carries_primary_value_or_literal_spans():
    _data, _current, _sources, _allowed, raw = partial_bundle()
    for invalid in (raw | {"value": "UNKNOWN"}, raw | {"sourceSpans": [{"start": 0, "end": 1}]}):
        with pytest.raises(ValueError):
            AnswerInterpretation.model_validate(invalid)


def test_empty_secondary_proposal_is_not_a_stageable_unresolved_answer():
    _data, _current, _sources, _allowed, raw = partial_bundle()
    raw["factualProposal"]["candidates"] = []
    with pytest.raises(ValueError):
        AnswerInterpretation.model_validate(raw)


def test_retained_unresolved_receipt_with_an_anchor_remains_readable_but_not_stageable():
    from legal_core.intake_answer_contracts import AnswerInterpretation

    old_wire = {"status": "UNRESOLVED", "value": None,
                "sourceSpans": [{"start": 0, "end": 8}], "reasonCode": "AMBIGUOUS"}
    restored = AnswerInterpretation.model_validate(old_wire)
    restored.validate_source("Не знаю.")
    assert restored.has_provisional_facts is False
    assert restored.model_dump(mode="json", by_alias=True) == old_wire
