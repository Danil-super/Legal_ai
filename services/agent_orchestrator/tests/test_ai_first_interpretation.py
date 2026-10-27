"""One existing-profile call may interpret the reply and retain other explicit facts."""

import asyncio
import json
from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest

from agent_orchestrator.hermes_client import HermesProtocolError
from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding
from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_factual_candidates import allowed_factual_questions, retained_answer_sources

DATA = {"intakeVersion": 2, "situationAreas": ["SERVICE"],
        "eventSummary": "Пациент обратился 15.10.2026 по поводу обслуживания."}
ANSWER = "Пригласили на осмотр."
AS_OF = date(2026, 10, 27)


def scenario(fault=None):
    current = derive_answer_question(DATA, "CLINIC_ACTIONS")
    sources = retained_answer_sources(uuid4(), 3, DATA, ANSWER)
    allowed = allowed_factual_questions(DATA, current)
    event = next(question for question in allowed if question.target_field == "eventDate")
    raw = {"status": "PROPOSED", "value": ["INVITED_FOR_EXAMINATION"],
           "sourceSpans": [{"start": 0, "end": len(ANSWER)}],
           "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1", "candidates": [{
               "question": event.model_dump(mode="json", by_alias=True),
               "value": {"date": "2026-10-15", "precision": "EXACT"}, "anchors": [{
                   "sourceId": str(sources[0].source_id),
                   "sourceSha256": sources[0].source_sha256, "start": 18, "end": 28,
               }],
           }]} }
    candidate = raw["factualProposal"]["candidates"][0]
    if fault == "foreign-source":
        candidate["anchors"][0]["sourceId"] = str(uuid4())
    elif fault == "stale-hash":
        candidate["anchors"][0]["sourceSha256"] = "b" * 64
    elif fault == "future":
        candidate["value"]["date"] = "2027-10-15"
    elif fault == "unallowed-question":
        candidate["question"] = current.model_dump(mode="json", by_alias=True)
    elif fault == "authority":
        raw["riskLevel"] = "LOW"
    return current, sources, allowed, raw


def run(fault=None, *, retained=True):
    current, sources, allowed, raw = scenario(fault)
    calls = []

    class Model:
        async def complete_json(self, **kwargs):
            calls.append(kwargs)
            return deepcopy(raw)

    async def invoke():
        understanding = AnswerUnderstanding(researcher=Model())
        return await understanding.interpret(ANSWER, current, DATA, AS_OF, **({
            "retained_sources": sources, "allowed_questions": allowed,
        } if retained else {}))

    return asyncio.run(invoke()), calls


def test_retained_interpretation_uses_one_call_and_preserves_additional_date():
    result, calls = run()
    assert result.factual_proposal.candidates[0].value == {
        "date": "2026-10-15", "precision": "EXACT",
    }
    assert len(calls) == 1
    sent = json.loads(calls[0]["user"])
    assert sent["confirmedContext"]["eventSummary"] == DATA["eventSummary"]
    assert sent["asOfDate"] == "2026-10-27"
    assert len(sent["retainedSources"]) == 2 and sent["allowedQuestions"]
    assert "untrusted" in calls[0]["system"]
    assert "Never infer" in calls[0]["system"]


@pytest.mark.parametrize("fault", ["foreign-source", "stale-hash", "future",
                                   "unallowed-question", "authority"])
def test_bad_additions_are_rejected_without_exposing_source_or_model_response(fault):
    with pytest.raises(HermesProtocolError) as caught:
        run(fault)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "15.10.2026" not in str(caught.value)


def test_legacy_context_cannot_smuggle_a_multi_fact_proposal():
    with pytest.raises(HermesProtocolError):
        run(retained=False)
