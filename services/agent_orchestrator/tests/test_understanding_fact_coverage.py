"""The initial model sees the complete factual vocabulary, not guessed negatives.

These inspect the real model-facing request and its runtime validation. The provider
response is synthetic abstention: passing them does NOT prove live-model recall.
"""

import asyncio
import json
import re
from copy import deepcopy

import httpx
import pytest
from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint, HermesProtocolError
from agent_orchestrator.intake_understanding import IntakeUnderstanding
from test_intake_understanding import run_model

from legal_core.case_api import ApiError
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from legal_core.intake_preparation_merge import merge_preparation_suggestions
from telegram_gateway.factual_safety_intake import screening_complete, screening_question
from telegram_gateway.guided_case_intake_v2 import V2_SUMMARY, next_missing_state

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


def _fields_payload(fields, source=SOURCE):
    # Offsets establish only a bounded literal anchor, not the truth of these values.
    return {**fields, "sourceSpans": {
        key: [{"start": 0, "end": len(source)}] for key in fields
    }}


def _full_screening(signal="NO"):
    return {
        "schemaVersion": SCREENING_VERSION, **dict.fromkeys(SCREENING_FIELDS, signal),
        "amount": "NOT_REQUESTED" if signal == "NO" else "UNKNOWN",
    }


def _draft():
    return {"intakeVersion": 2, "eventSummary": SOURCE,
            "incomingSourceStatus": "NOT_ATTACHED", "caseMaterialsStatus": "NOT_ATTACHED"}


def test_explicit_complete_proposal_survives_core_merge_and_skips_known_questions():
    fields = {
        "incomingKind": "COMPLAINT", "situationAreas": ["SERVICE"],
        "eventDate": {"date": "2026-10-18", "precision": "EXACT"},
        "conflictStage": "FIRST", "clinicActions": ["NOTHING_YET"],
        "healthSignals": ["NO_KNOWN_INFORMATION"], "safetyScreening": _full_screening(),
        "analysisClarificationAnswers": {"answers": [{
            "questionId": "RESPONSE_PURPOSE", "serviceSlot": None,
            "value": "RECEIVE_AND_EXPLAIN",
        }]},
    }
    proposal = run_model(SOURCE, _fields_payload(fields), [])
    draft = _draft()
    before = deepcopy(draft)
    merged = merge_preparation_suggestions(draft, proposal, SOURCE)
    assert draft == before
    assert merged == {**before, **fields}
    assert screening_complete(merged)
    assert screening_question(merged) is None
    assert next_missing_state(merged) == V2_SUMMARY
    assert not {"confirmed", "sourceSpans", "riskLevel", "caseId"} & merged.keys()


@pytest.mark.parametrize("field", SCREENING_FIELDS)
def test_partial_screening_does_not_default_other_fields_or_skip_missing_questions(field):
    screening = {"schemaVersion": SCREENING_VERSION, field: "NO"}
    if field == "moneyRequested":
        screening["amount"] = "NOT_REQUESTED"
    fields = {"safetyScreening": screening}
    proposal = run_model(SOURCE, _fields_payload(fields), [])
    merged = merge_preparation_suggestions(_draft(), proposal, SOURCE)
    assert merged["safetyScreening"] == screening
    assert "healthSignals" not in merged
    assert not screening_complete(merged)
    assert screening_question(merged)[0] == next(key for key in SCREENING_FIELDS if key != field)


def test_explicit_unknown_screening_remains_unknown_not_negative_or_an_unanswered_field():
    source = (
        "Сведения об ухудшении неизвестны. О госпитализации неизвестно. "
        "Об обращении представителя неизвестно. О письменных требованиях неизвестно. "
        "О документе суда неизвестно. Об обращении в орган неизвестно. "
        "О денежном требовании и сумме неизвестно."
    )
    screening = _full_screening("UNKNOWN")
    proposal = run_model(source, _fields_payload({"safetyScreening": screening}, source), [])
    merged = merge_preparation_suggestions(_draft(), proposal, source)
    assert merged["safetyScreening"] == screening
    assert screening_complete(merged)
    assert screening_question(merged) is None
    assert "healthSignals" not in merged


def test_schema_version_alone_does_not_turn_absent_details_into_unknown_or_no():
    screening = {"schemaVersion": SCREENING_VERSION}
    proposal = run_model(SOURCE, _fields_payload({"safetyScreening": screening}), [])
    merged = merge_preparation_suggestions(_draft(), proposal, SOURCE)
    assert merged["safetyScreening"] == screening
    assert screening_question(merged)[0] == SCREENING_FIELDS[0]
    assert not screening_complete(merged)


def test_abstention_leaves_screening_absent_and_asks_first_missing_fact():
    proposal = run_model(SOURCE, {"sourceSpans": {}}, [])
    merged = merge_preparation_suggestions(_draft(), proposal, SOURCE)
    assert merged == _draft()
    assert "safetyScreening" not in merged
    assert screening_question(merged)[0] == SCREENING_FIELDS[0]


def test_explicit_requested_money_remains_integer_kopecks_through_core_merge():
    source = SOURCE.replace("Денег не требуют.", "Пациент просит 12 345,67 рубля.")
    money = {"amountKopecks": 1_234_567, "currency": "RUB"}
    screening = {"schemaVersion": SCREENING_VERSION, "moneyRequested": "YES", "amount": money}
    proposal = run_model(source, _fields_payload({"safetyScreening": screening}, source), [])
    merged = merge_preparation_suggestions(_draft(), proposal, source)
    assert merged["safetyScreening"] == screening
    assert merged["safetyScreening"]["amount"]["amountKopecks"] == 1_234_567


@pytest.mark.parametrize("screening", [
    {"moneyRequested": "NO", "amount": "UNKNOWN"},
    {"moneyRequested": "YES", "amount": "NOT_REQUESTED"},
    {"moneyRequested": "UNKNOWN", "amount": "NOT_REQUESTED"},
    {"moneyRequested": "NO", "amount": {"amountKopecks": 1, "currency": "RUB"}},
    {"amount": {"amountKopecks": 1, "currency": "RUB"}},
    {"moneyRequested": "YES", "amount": {"amountKopecks": 1.5, "currency": "RUB"}},
    {"moneyRequested": "YES", "amount": {"amountKopecks": True, "currency": "RUB"}},
    {"moneyRequested": "YES", "amount": {"amountKopecks": 0, "currency": "RUB"}},
    {"moneyRequested": "YES", "amount": {"amountKopecks": 100, "currency": "USD"}},
    {"hospitalizationReported": False},
    {"hospitalizationReported": "ABSENT"},
    {"untrustedNewField": "NO"},
])
def test_invalid_screening_values_still_fail_runtime_contract_without_source_echo(screening):
    payload = _fields_payload({"safetyScreening": {"schemaVersion": SCREENING_VERSION,
                                                  **screening}})
    with pytest.raises(HermesProtocolError) as caught:
        run_model(SOURCE, payload, [])
    assert SOURCE not in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize("span", [None, [], [{"start": 0, "end": len(SOURCE) + 1}],
                                  [{"start": True, "end": 20}]])
def test_screening_requires_its_own_valid_source_anchor(span):
    payload = _fields_payload({"safetyScreening": _full_screening()})
    if span is None:
        payload["sourceSpans"].pop("safetyScreening")
    else:
        payload["sourceSpans"]["safetyScreening"] = span
    with pytest.raises(HermesProtocolError):
        run_model(SOURCE, payload, [])


@pytest.mark.parametrize("existing", ["YES", "UNKNOWN"])
def test_new_negative_suggestion_cannot_overwrite_existing_attempt(existing):
    draft = {**_draft(), "safetyScreening": {
        "schemaVersion": SCREENING_VERSION, "hospitalizationReported": existing,
    }}
    before = deepcopy(draft)
    proposal = run_model(SOURCE, _fields_payload({"safetyScreening": {
        "schemaVersion": SCREENING_VERSION, "hospitalizationReported": "NO",
    }}), [])
    with pytest.raises(ApiError) as caught:
        merge_preparation_suggestions(draft, proposal, SOURCE)
    assert draft == before
    assert caught.value.code == "PREPARATION_MERGE_CONFLICT"
