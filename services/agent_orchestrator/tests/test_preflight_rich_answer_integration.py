"""Exercise the actual model adapter with the complete preflight target universe."""

import asyncio
import json
from datetime import date
from uuid import uuid4

import httpx
import pytest
from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding
from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_factual_candidates import (
    allowed_factual_questions,
    retained_answer_sources,
)


@pytest.mark.parametrize("service_count", [2, 5])
@pytest.mark.parametrize("state", ["HEALTH", "ANALYSIS_CLARIFICATION"])
@pytest.mark.parametrize("additional_facts", [False, True])
def test_actual_agent_accepts_rich_preflight_targets_without_using_report_request_batch(
    service_count, state, additional_facts,
):
    services = [f"Синтетическая услуга {slot}" for slot in range(service_count)]
    clauses = [f"{service} оказана 01.10.2026, платно." for service in services]
    narrative = " ".join(clauses) + " Нужно ответить на запрос."
    data = {
        "intakeVersion": 2, "incomingKind": "MESSAGE_OR_REQUEST",
        "situationAreas": ["TREATMENT"], "affectedServices": services,
        "eventSummary": narrative,
    }
    question = derive_answer_question(data, state)
    allowed = allowed_factual_questions(data, question)
    source = ("Сведений по последствиям нет" if state == "HEALTH"
              else "Эта услуга была оказана платно")
    value = ["NO_KNOWN_INFORMATION"] if state == "HEALTH" else "PAID"
    sources = retained_answer_sources(uuid4(), 3, data, source)
    response = {
        "status": "PROPOSED", "value": value,
        "sourceSpans": [{"start": 0, "end": len(source)}],
    }
    if additional_facts:
        candidates = []
        for target in allowed:
            request = target.clarification_request
            if request is None:
                continue
            if request.question_id == "RESPONSE_PURPOSE":
                quote, addition = "Нужно ответить на запрос", "ANSWER_REQUEST"
            else:
                quote = clauses[request.service_slot]
                addition = "PAID" if request.question_id == "SERVICE_PAYMENT_BASIS" else {
                    "date": "2026-10-01", "precision": "EXACT",
                }
            start = narrative.index(quote)
            candidates.append({
                "question": target.model_dump(mode="json", by_alias=True), "value": addition,
                "anchors": [{"sourceId": str(sources[0].source_id),
                             "sourceSha256": sources[0].source_sha256,
                             "start": start, "end": start + len(quote)}],
            })
        response["factualProposal"] = {
            "schemaVersion": "ai-first-factual-proposal.v1", "candidates": candidates,
        }
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": json.dumps(response)}, "finish_reason": "stop"}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            researcher = HermesClient(HermesEndpoint(
                "http://researcher:8642", "synthetic-key", model="existing-researcher",
                timeout_seconds=65,
            ), client=http)
            return await AnswerUnderstanding(researcher=researcher).interpret(
                source, question, data, date(2026, 10, 28),
                retained_sources=sources, allowed_questions=allowed,
            )

    result = asyncio.run(run())
    assert result.status == "PROPOSED" and result.value == value
    assert len(calls) <= 1
    if additional_facts:
        assert len(result.factual_proposal.candidates) == 2 * service_count + (
            1 if state == "HEALTH" else 0
        )
    assert "analysisClarificationRequests" not in data
