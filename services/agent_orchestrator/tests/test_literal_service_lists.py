"""The list format requested by the service question must not depend on model interpretation."""

import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding
from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_factual_candidates import allowed_factual_questions, retained_answer_sources
from test_intake_answer_interpretation import AS_OF, DATA, run_model, unresolved
from test_literal_factual_answers import _no_model


@pytest.mark.parametrize("source,values", [
    ("Ортодонтическое лечение с использованием элайнеров",
     ["Ортодонтическое лечение с использованием элайнеров"]),
    ("Ортодонтическое лечение", ["Ортодонтическое лечение"]),
    ("неизвестно", ["неизвестно"]),
    ("Лечение кариеса; профессиональная гигиена", ["Лечение кариеса", "профессиональная гигиена"]),
    ("  Осмотр;\nлечение кариеса; установка коронки; ",
     ["Осмотр", "лечение кариеса", "установка коронки"]),
    ("Осмотр; " + "а" * 120, ["Осмотр", "а" * 120]),
])
def test_explicit_service_list_is_a_source_bound_proposal_without_model_io(source, values):
    data = {key: value for key, value in DATA.items() if key != "affectedServices"}
    assert _no_model(source, "SERVICES", data) == values


@pytest.mark.parametrize("source", [
    "Осмотр; осмотр; осмотр; осмотр; осмотр; осмотр",
    "Осмотр; " + "а" * 121,
    "Осмотр; Осмотр",
    "; ;",
    "Осмотр; https://example.invalid",
])
def test_invalid_service_lists_are_not_silently_accepted_or_truncated(source):
    calls = []
    data = {key: value for key, value in DATA.items() if key != "affectedServices"}
    result = run_model(source, unresolved(), state="SERVICES", data=data, calls=calls)
    assert result.status == "UNRESOLVED" and result.value is None
    assert len(calls) == 1


@pytest.mark.parametrize("source", [
    "Пациент отказался от ортодонтического лечения",
    "Ортодонтическое лечение, но пациент передумал",
    "https://example.invalid",
])
def test_non_label_service_reply_still_requires_interpretation(source):
    calls = []
    data = {key: value for key, value in DATA.items() if key != "affectedServices"}
    result = run_model(source, unresolved(), state="SERVICES", data=data, calls=calls)
    assert result.status == "UNRESOLVED" and len(calls) == 1


def test_retained_fact_extraction_can_run_without_repeating_a_literal_service_list():
    data = {key: value for key, value in DATA.items() if key != "affectedServices"}
    source = "Лечение кариеса; профессиональная гигиена"
    current = derive_answer_question(data, "SERVICES")
    retained = retained_answer_sources(uuid4(), 2, data, source)
    allowed = allowed_factual_questions(data, current)
    calls = []

    class UnresolvedModel:
        async def complete_json(self, **kwargs):
            calls.append(kwargs)
            return unresolved()

    async def scenario():
        return await AnswerUnderstanding(researcher=UnresolvedModel()).interpret(
            source, current, data, AS_OF, retained_sources=retained, allowed_questions=allowed,
        )

    before = deepcopy(data)
    result = asyncio.run(scenario())
    assert result.status == "PROPOSED"
    assert result.value == ["Лечение кариеса", "профессиональная гигиена"]
    assert [(span.start, span.end) for span in result.source_spans] == [(0, len(source))]
    assert data == before
    assert len(calls) == 1, "Preserve model extraction from the retained initial description"


def test_provider_unavailable_can_keep_only_the_valid_literal_service_primary():
    from agent_orchestrator.hermes_client import HermesUnavailable

    data = {key: value for key, value in DATA.items() if key != "affectedServices"}
    source = "Лечение кариеса; профессиональная гигиена"
    current = derive_answer_question(data, "SERVICES")
    retained = retained_answer_sources(uuid4(), 2, data, source)
    allowed = allowed_factual_questions(data, current)

    class UnavailableModel:
        async def complete_json(self, **kwargs):
            raise HermesUnavailable()

    async def scenario():
        return await AnswerUnderstanding(researcher=UnavailableModel()).interpret(
            source, current, data, AS_OF, retained_sources=retained, allowed_questions=allowed,
        )

    result = asyncio.run(scenario())
    assert result.status == "PROPOSED" and result.value == source.split("; ")
    assert result.factual_proposal is None


def test_literal_fallback_keeps_source_validation_before_unresolved_conversion():
    from agent_orchestrator.hermes_client import HermesProtocolError

    data = {key: value for key, value in DATA.items() if key != "affectedServices"}
    source = "Лечение кариеса; профессиональная гигиена"
    current = derive_answer_question(data, "SERVICES")
    retained = retained_answer_sources(uuid4(), 2, data, source)
    allowed = allowed_factual_questions(data, current)

    class CorruptModel:
        async def complete_json(self, **kwargs):
            return unresolved() | {"sourceSpans": [{"start": 0, "end": len(source) + 1}]}

    async def scenario():
        return await AnswerUnderstanding(researcher=CorruptModel()).interpret(
            source, current, data, AS_OF, retained_sources=retained, allowed_questions=allowed,
        )

    with pytest.raises(HermesProtocolError):
        asyncio.run(scenario())


def test_literal_service_fallback_preserves_a_supported_date_from_the_initial_description():
    from legal_core.intake_factual_candidates import apply_factual_answer

    narrative = "Лечение кариеса оказано 01.10.2026 платно. Возник спор об оплате."
    data = {key: value for key, value in DATA.items() if key != "affectedServices"}
    data["eventSummary"] = narrative
    source = "Лечение кариеса; профессиональная гигиена"
    current = derive_answer_question(data, "SERVICES")
    retained = retained_answer_sources(uuid4(), 2, data, source)
    allowed = allowed_factual_questions(data, current)
    target = next(item for item in allowed if item.clarification_request is not None
                  and item.clarification_request.question_id == "SERVICE_PROVIDED_DATE"
                  and item.clarification_request.service_slot == 0)
    quote = "Лечение кариеса оказано 01.10.2026 платно"
    date_value = {"date": "2026-10-01", "precision": "EXACT"}
    raw = unresolved() | {"factualProposal": {
        "schemaVersion": "ai-first-factual-proposal.v1", "candidates": [{
            "question": target.model_dump(mode="json", by_alias=True), "value": date_value,
            "anchors": [{"sourceId": str(retained[0].source_id),
                         "sourceSha256": retained[0].source_sha256,
                         "start": 0, "end": len(quote)}],
        }],
    }}

    class Model:
        async def complete_json(self, **kwargs):
            return raw

    async def scenario():
        return await AnswerUnderstanding(researcher=Model()).interpret(
            source, current, data, AS_OF, retained_sources=retained, allowed_questions=allowed,
        )

    result = asyncio.run(scenario())
    merged = apply_factual_answer(data, current, result, source, retained, as_of_date=AS_OF)
    assert merged["affectedServices"] == source.split("; ")
    assert merged["analysisClarificationAnswers"] == {"answers": [{
        "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0, "value": date_value,
    }]}
