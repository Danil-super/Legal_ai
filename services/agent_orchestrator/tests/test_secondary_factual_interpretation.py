"""The actual bounded model adapter supports source-bound secondary-only answers."""

import pytest
from test_secondary_factual_staging import ANSWER, AS_OF, DATE, partial_bundle


def model_reply(response, data, current, sources, allowed, *, calls=None):
    import asyncio
    import json

    import httpx
    from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
    from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding

    def respond(request):
        if calls is not None:
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
                ANSWER, current, data, AS_OF, retained_sources=sources, allowed_questions=allowed,
            )
    return asyncio.run(run())


def test_real_model_adapter_retains_secondary_only_without_manufacturing_primary():
    data, current, sources, allowed, raw = partial_bundle()
    calls = []
    result = model_reply(raw, data, current, sources, allowed, calls=calls)
    assert result.status == "UNRESOLVED" and result.value is None and result.source_spans == []
    assert result.has_provisional_facts and len(calls) == 1
    assert result.factual_proposal.candidates[0].value == DATE
    assert "leaving the primary unresolved" in calls[0]["messages"][0]["content"]


def test_real_model_adapter_rejects_unresolved_secondary_target_outside_reserved_scope():
    from agent_orchestrator.hermes_client import HermesProtocolError

    data, current, sources, allowed, raw = partial_bundle()
    raw["factualProposal"]["candidates"][0]["question"]["clarificationRequest"]["serviceSlot"] = 1
    with pytest.raises(HermesProtocolError):
        model_reply(raw, data, current, sources, allowed)
