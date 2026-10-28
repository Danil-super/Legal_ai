"""Real streaming model clients share the route wall; intake keeps its shorter cap."""

import asyncio
from dataclasses import replace
import json

import httpx
from fastapi.testclient import TestClient
import pytest

from agent_orchestrator import main
from agent_orchestrator.hermes_client import HermesClient
from test_main import CASE_ID, FRAGMENT_ID, IDEMPOTENCY_KEY, INTERNAL_KEY, FakeLegalCore


def _settings():
    return main.ServiceSettings(INTERNAL_KEY, "http://core.test", "http://researcher.test",
                                "research-key", "existing-high-r", "http://reviewer.test",
                                "review-key", "existing-high-v")


def _stream(body):
    frame = {"object": "chat.completion.chunk", "choices": [{
        "index": 0, "delta": {"content": json.dumps(body)}, "finish_reason": "stop",
    }]}
    return httpx.Response(200, content="data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n",
                          headers={"content-type": "text/event-stream"})


def _exercise(monkeypatch, research_delay, review_delay):
    # Keep the actual nested timeout/streaming/cancellation code, with a short
    # 1s former per-call cap and a 2s unchanged overall wall for the regression.
    monkeypatch.setattr(main, "HERMES_STAGE_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(main, "ANALYSIS_WALL_TIMEOUT_SECONDS", 2)
    stages, cancelled = [], []

    async def respond(request):
        stage = request.url.host
        stages.append(stage)
        try:
            await asyncio.sleep(research_delay if stage == "researcher.test" else review_delay)
        except asyncio.CancelledError:
            cancelled.append(stage)
            raise
        if stage == "researcher.test":
            return _stream({"claims": [{
                "claimId": "action-1", "kind": "ACTION", "text": "Зафиксировать обращение.",
                "evidenceFragmentIds": [str(FRAGMENT_ID)], "requiredFactKeys": [],
            }], "internalRecommendations": [], "patientDraft": None, "clarificationRequests": []})
        return _stream({"reviews": [{"claimId": "action-1", "verdict": "SUPPORTED",
                                     "reviewedFragmentIds": [str(FRAGMENT_ID)]}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(main, "HermesClient", lambda endpoint: HermesClient(endpoint, client=http))
    dependencies = main.build_dependencies(_settings())
    core = FakeLegalCore()
    dependencies = replace(dependencies, legal_core=core)
    with TestClient(main.create_app(settings=_settings(), dependencies=dependencies)) as client:
        response = client.post(f"/v1/cases/{CASE_ID}/analyze", headers={
            "X-Agent-Internal-Key": INTERNAL_KEY, "X-Telegram-User-Id": "123",
            "Idempotency-Key": str(IDEMPOTENCY_KEY),
        })
        # Cancellation is awaited, so leaving the request cannot submit a late plan.
        assert core.submit_calls == int(response.status_code == 200)
    asyncio.run(http.aclose())
    return response, core, stages, cancelled


def test_slow_first_pass_can_use_unused_reviewer_time(monkeypatch):
    response, core, stages, cancelled = _exercise(monkeypatch, 1.1, 0.05)
    assert response.status_code == 200, response.text
    assert core.submit_calls == 1
    assert stages == ["researcher.test", "reviewer.test"] and not cancelled


def test_reviewer_receives_remaining_wall_not_a_fresh_whole_budget(monkeypatch):
    response, core, stages, cancelled = _exercise(monkeypatch, 0.8, 1.4)
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": {"code": "ANALYSIS_PROVIDER_UNAVAILABLE"}}
    assert stages == ["researcher.test", "reviewer.test"]
    assert cancelled == ["reviewer.test"] and core.submit_calls == 0


@pytest.mark.parametrize("component", ["understanding", "answer_understanding"])
def test_intake_still_uses_separate_shorter_client(component):
    dependencies = main.build_dependencies(_settings())
    intake = getattr(dependencies, component)._researcher
    assert intake.endpoint.timeout_seconds == main.HERMES_STAGE_TIMEOUT_SECONDS == 65
    assert intake is not dependencies.reasoning._researcher
    assert dependencies.reasoning._researcher.endpoint.timeout_seconds == 115
    assert dependencies.reasoning._reviewer.endpoint.timeout_seconds == 115
