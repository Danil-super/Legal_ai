"""Whole short replies skip the model only after current-question/source validation."""

import asyncio
from copy import deepcopy

import pytest

from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from legal_core.intake_answer_contracts import derive_answer_question
from test_intake_answer_interpretation import AS_OF, DATA, run_model, unresolved


def _screening(field):
    return DATA | {"safetyScreening": {"schemaVersion": SCREENING_VERSION,
        **{key: "NO" for key in SCREENING_FIELDS if key != field}}}


def _clarification(question_id, *, slot=0):
    return DATA | {"analysisClarificationRequests": [{
        "questionId": question_id, "serviceSlot": slot,
    }]}


def _no_model(source, state, data):
    class ModelMustNotRun:
        async def complete_json(self, **kwargs):
            raise AssertionError("a complete literal factual answer must not wait for a model")

    async def invoke():
        question = derive_answer_question(data, state)
        assert question is not None
        return await AnswerUnderstanding(researcher=ModelMustNotRun()).interpret(
            source, question, data, AS_OF,
        )

    before = deepcopy(data)
    result = asyncio.run(invoke())
    assert data == before  # This is a proposal, never confirmation or storage.
    assert result.status == "PROPOSED" and result.factual_proposal is None
    assert [(span.start, span.end) for span in result.source_spans] == [(0, len(source))]
    result.validate_source(source)
    return result.value


@pytest.mark.parametrize("field", SCREENING_FIELDS)
@pytest.mark.parametrize("source,value", [("Да", "YES"), ("нет", "NO"), ("  НЕТ\n", "NO")])
def test_literal_safety_reply_is_only_the_current_signal(field, source, value):
    assert _no_model(source, "SAFETY", _screening(field)) == value


@pytest.mark.parametrize("source", ["2026-10-15", "15.10.2026", " 15.10.2026\n"])
@pytest.mark.parametrize("question_id", [None, "SERVICE_PROVIDED_DATE"])
def test_complete_exact_date_keeps_the_current_event_or_service_identity(source, question_id):
    data = DATA if question_id is None else _clarification(question_id)
    state = "EVENT_DATE" if question_id is None else "ANALYSIS_CLARIFICATION"
    assert _no_model(source, state, data) == {"date": "2026-10-15", "precision": "EXACT"}


@pytest.mark.parametrize("source,value", [("платно", "PAID"), ("ОМС", "PUBLICLY_COVERED"),
                                          ("по ОМС", "PUBLICLY_COVERED")])
def test_literal_payment_label_keeps_one_existing_service_slot(source, value):
    data = _clarification("SERVICE_PAYMENT_BASIS", slot=1) | {
        "affectedServices": ["установка коронки", "профессиональная чистка"],
    }
    assert _no_model(source, "ANALYSIS_CLARIFICATION", data) == value


@pytest.mark.parametrize("state,data,source", [
    ("SAFETY", DATA, "Да, но госпитализации не было"),
    ("SAFETY", DATA, "Не да"),
    ("SAFETY", DATA, "Нет или да"),
    ("EVENT_DATE", DATA, "15.10"),
    ("EVENT_DATE", DATA, "вчера"),
    ("EVENT_DATE", DATA, "15.10.2026 или 16.10.2026"),
    ("EVENT_DATE", DATA, "31.02.2026"),
    ("EVENT_DATE", DATA, "2026-10-17"),
    ("EVENT_DATE", DATA, "Да"),
    ("INCOMING", DATA, "Нет"),
    ("INCOMING", DATA, "15.10.2026"),
    ("EVENT_DATE", DATA, "ОМС"),
    ("ANALYSIS_CLARIFICATION", _clarification("SERVICE_PAYMENT_BASIS"),
     "Коронка платно, чистка по ОМС"),
    ("ANALYSIS_CLARIFICATION", _clarification("RESPONSE_PURPOSE", slot=None), "платно"),
    ("ANALYSIS_CLARIFICATION", _clarification("SERVICE_PROVIDED_DATE"), "2026-10-17"),
])
def test_narrative_ambiguous_invalid_future_or_off_question_input_keeps_model_path(
    state, data, source,
):
    calls = []
    result = run_model(source, unresolved(), state=state, data=data, calls=calls)
    assert len(calls) == 1 and result.status == "UNRESOLVED" and result.value is None


def test_literal_fast_path_cannot_accept_a_stale_service_question():
    old = _clarification("SERVICE_PAYMENT_BASIS")
    question = derive_answer_question(old, "ANALYSIS_CLARIFICATION")
    data = old | {"affectedServices": ["другая услуга"]}
    calls = []
    result = run_model("платно", unresolved(), state="ANALYSIS_CLARIFICATION", data=data,
                       q=question, calls=calls)
    assert len(calls) == 1 and result.status == "UNRESOLVED"


def test_private_input_is_rejected_before_literal_or_model_processing():
    calls = []
    with pytest.raises(ValueError):
        run_model("Да, +7 999 123-45-67", unresolved(), state="SAFETY", calls=calls)
    assert not calls


@pytest.mark.parametrize("corrupt", [False, True])
def test_retained_source_validation_precedes_even_a_literal_reply(corrupt):
    from uuid import uuid4

    from legal_core.intake_factual_candidates import (
        allowed_factual_questions,
        retained_answer_sources,
    )

    data = _screening("hospitalizationReported") | {"analysisClarificationAnswers": {
        "answers": [
            {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "UNKNOWN"},
            {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
             "value": {"date": None, "precision": "UNKNOWN"}},
            {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "UNKNOWN"},
        ],
    }}
    question = derive_answer_question(data, "SAFETY")
    source = "Нет"
    retained = retained_answer_sources(uuid4(), 3, data, source)
    if corrupt:
        retained[0] = retained[0].model_copy(update={"source_sha256": "0" * 64})

    class ModelMustNotRun:
        async def complete_json(self, **kwargs):
            raise AssertionError("source validation and literals do not require a model")

    async def invoke():
        return await AnswerUnderstanding(researcher=ModelMustNotRun()).interpret(
            source, question, data, AS_OF, retained_sources=retained,
            allowed_questions=allowed_factual_questions(data, question),
        )

    if corrupt:
        with pytest.raises(ValueError):
            asyncio.run(invoke())
    else:
        result = asyncio.run(invoke())
        assert result.status == "PROPOSED" and result.value == "NO"
        assert result.factual_proposal is None
        assert "hospitalizationReported" not in data["safetyScreening"]
