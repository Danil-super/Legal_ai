"""The initial model sees the complete factual vocabulary, not guessed negatives.

These inspect the real model-facing request and its runtime validation. The provider
response is synthetic abstention: passing them does NOT prove live-model recall.
"""

import asyncio
import json
import re

import httpx
import pytest
from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
from agent_orchestrator.intake_understanding import IntakeUnderstanding
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION

SOURCE = (
    "Учебный синтетический кейс. 18.10.2026 пациент впервые устно пожаловался "
    "на ожидание приёма 25 минут. Клиника пока ничего не сделала. "
    "Сведений об ухудшении здоровья нет. Госпитализации не было. "
    "Представитель не обращался. Письменных требований не поступало. "
    "Документа суда или органа нет. Обращение в орган не упоминалось. "
    "Денег не требуют. Нужно принять обращение и объяснить ситуацию."
)


@pytest.fixture(scope="module")
def initial_request():
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "message": {"role": "assistant", "content": '{"sourceSpans":{}}'},
            "finish_reason": "stop",
        }]})

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            model = HermesClient(
                HermesEndpoint("http://researcher:8642", "synthetic-key",
                               model="existing-researcher", timeout_seconds=65),
                client=http,
            )
            result = await IntakeUnderstanding(researcher=model).understand(SOURCE)
            assert result.model_dump(by_alias=True, exclude_none=True) == {"sourceSpans": {}}

    asyncio.run(invoke())
    assert len(calls) == 1
    assert set(calls[0]) == {"model", "messages", "stream"}
    assert json.loads(calls[0]["messages"][1]["content"]) == {"source": SOURCE}
    return calls[0]


def _schema(request):
    return json.loads(request["messages"][0]["content"].split("JSON Schema:\n", 1)[1])


def _resolve(value, schema):
    if "$ref" in value:
        assert value["$ref"].startswith("#/$defs/")
        return _resolve(schema["$defs"][value["$ref"].removeprefix("#/$defs/")], schema)
    if "anyOf" in value:
        alternatives = [item for item in value["anyOf"] if item.get("type") != "null"]
        if len(alternatives) == 1:
            return _resolve(alternatives[0], schema)
    return value


def test_initial_prompt_does_not_order_hermes_to_discard_explicit_negative_facts(initial_request):
    instructions = initial_request["messages"][0]["content"].split("JSON Schema:", 1)[0]
    assert not re.search(r"Omit[^.]*\bnegated\b", instructions, re.IGNORECASE), (
        "Explicit 'no hospitalization/no money request' must remain grounded proposals; "
        "absence and ambiguity still must not become negative facts."
    )


def test_initial_model_schema_exposes_closed_but_partial_safety_screening(initial_request):
    schema = _schema(initial_request)
    screening = _resolve(schema["properties"]["safetyScreening"], schema)
    assert screening.get("additionalProperties") is False, (
        "A generic JSON dict hides safety fields and their permitted values from Hermes."
    )
    assert set(screening["properties"]) == {"schemaVersion", "amount", *SCREENING_FIELDS}
    assert set(screening["required"]) == {"schemaVersion"}, (
        "Unmentioned safety fields must remain omittable, never default to NO/UNKNOWN."
    )
    assert screening["properties"]["schemaVersion"]["const"] == SCREENING_VERSION
    for field in SCREENING_FIELDS:
        assert set(_resolve(screening["properties"][field], schema)["enum"]) == {
            "YES", "NO", "UNKNOWN",
        }


def test_initial_model_schema_explains_rubles_and_missing_money_without_open_json(initial_request):
    schema = _schema(initial_request)
    screening = _resolve(schema["properties"]["safetyScreening"], schema)
    assert "properties" in screening, "Money representation must be visible to the model."
    amount = _resolve(screening["properties"]["amount"], schema)
    variants = [_resolve(item, schema) for item in amount["anyOf"]]
    alternatives = {choice for item in variants for choice in item.get("enum", [])}
    assert alternatives == {"UNKNOWN", "NOT_REQUESTED"}
    money = next(item for item in variants if item.get("type") == "object")
    assert money["additionalProperties"] is False
    assert set(money["required"]) == {"amountKopecks", "currency"}
    assert money["properties"]["currency"]["const"] == "RUB"
    assert money["properties"]["amountKopecks"]["type"] == "integer"
    assert money["properties"]["amountKopecks"]["exclusiveMinimum"] == 0


def test_initial_model_can_see_existing_response_purpose_choices(initial_request):
    system = initial_request["messages"][0]["content"]
    assert "RESPONSE_PURPOSE" in system
    assert "RECEIVE_AND_EXPLAIN" in system, (
        "The generic string answer schema does not teach Hermes the existing purpose enum."
    )
    assert "ANSWER_REQUEST" in system
