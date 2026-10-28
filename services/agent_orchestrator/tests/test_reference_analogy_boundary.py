"""Analogies may inform comparison, never supply authority, facts or private provenance."""
import asyncio
import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_orchestrator.contracts import ReferenceAnalogyItem
from agent_orchestrator.hermes_client import HermesProtocolError
from agent_orchestrator.reasoning import LegalReasoningOrchestrator, _research_prompt
from test_reasoning import FakeHermes, _claim_response, _projection, _review_response


def analogy(**change):
    return ReferenceAnalogyItem(**{
        "analogyId": "example-1", "groupKey": "healthcare",
        "evaluationAsOfDate": "2026-10-08",
        "scenarioText": "Учебный пример: иной спор, сумма 9999 рублей.", **change})


def reason(projection, proposal=None):
    researcher = FakeHermes(name="researcher", response=proposal or _claim_response())
    reviewer = FakeHermes(name="reviewer", response=_review_response())
    runner = LegalReasoningOrchestrator(researcher=researcher, reviewer=reviewer)
    return runner, researcher, reviewer


def test_absent_examples_preserve_existing_wire_and_prompt():
    original = _projection()
    explicit_empty = original.model_copy(update={"reference_examples": []})
    assert explicit_empty.model_dump_json(by_alias=True) == original.model_dump_json(by_alias=True)
    assert "referenceExamples" not in original.model_dump(by_alias=True)
    assert _research_prompt(original) == _research_prompt(explicit_empty)


def test_both_passes_receive_same_nonauthoritative_examples_without_inheriting_facts():
    projection = _projection().model_copy(update={"reference_examples": [analogy()]})
    before = projection.facts.copy()
    runner, researcher, reviewer = reason(projection)
    asyncio.run(runner.reason(projection))
    research = json.loads(researcher.users[0])
    review = json.loads(reviewer.users[0])
    assert research["referenceExamples"] == review["referenceExamples"]
    item = research["referenceExamples"][0]
    assert set(item) == {"analogyId", "groupKey", "scenarioText", "evaluationAsOfDate",
                         "dateBasis", "purpose"}
    assert item["purpose"] == "NON_AUTHORITATIVE_ANALOGY"
    assert item["dateBasis"] == "EVALUATION_DATE_NOT_EVENT_DATE"
    assert research["facts"] == review["facts"] == before == projection.facts
    for prompt in (researcher.systems[0], reviewer.systems[0]):
        assert "не являются" in prompt and "referenceExamples" in prompt
    schema = json.loads(researcher.systems[0].rsplit("JSON Schema ответа:\n", 1)[1])
    ids = schema["$defs"]["ClaimProposal"]["properties"]["evidenceFragmentIds"]
    legal_ids = ids["items"]["enum"]
    assert legal_ids == [str(projection.evidence[0].fragment_id)]


def test_reference_citation_cannot_become_legal_evidence_even_if_researcher_asserts_it():
    projection = _projection().model_copy(update={"reference_examples": [analogy()]})
    proposal = _claim_response(UUID("88888888-8888-4888-8888-888888888888"))
    runner, researcher, reviewer = reason(projection, proposal)
    with pytest.raises(HermesProtocolError, match="outside approved legal evidence"):
        asyncio.run(runner.reason(projection))
    assert researcher.calls == 1 and reviewer.calls == 0


@pytest.mark.parametrize("fault", ["pii", "budget", "count", "duplicate"])
def test_mutated_analogy_payload_fails_before_any_model_call(fault):
    item = analogy()
    projection = _projection().model_copy(update={"reference_examples": [item]})
    if fault == "pii":
        item.__dict__["scenario_text"] = "Учебный контакт patient@example.invalid"
    elif fault == "budget":
        projection.reference_examples = [item, analogy(analogyId="example-2")]
        for example in projection.reference_examples:
            example.__dict__["scenario_text"] = "Учебный пример. " * 450
    elif fault == "count":
        projection.reference_examples = [item] * 4
    else:
        projection.reference_examples = [item, item]
    runner, researcher, reviewer = reason(projection)
    with pytest.raises(HermesProtocolError) as caught:
        asyncio.run(runner.reason(projection))
    assert researcher.calls == reviewer.calls == 0
    assert "patient@example.invalid" not in str(caught.value)


@pytest.mark.parametrize("field,value", [("source", {"versionId": "private"}),
    ("expectedRoute", "INTERNAL_DRAFT"), ("purpose", "LEGAL_EVIDENCE"),
    ("dateBasis", "LAWYER_CURRENT_COPY")])
def test_model_analogy_contract_refuses_private_metadata_and_authority(field, value):
    with pytest.raises(ValidationError):
        analogy(**{field: value})
