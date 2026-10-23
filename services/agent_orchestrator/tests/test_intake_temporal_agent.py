"""Approved temporal-reference contract; no live model or patient data.

Only the provider boundary is stubbed. These tests prove reference forwarding and
date bounds, not the live model's recall or correct service/complaint binding.
"""

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_orchestrator.intake_understanding as understanding_module
from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint, HermesProtocolError
from agent_orchestrator.intake_understanding import IntakeUnderstanding
from agent_orchestrator.intake_understanding_api import create_understanding_router
from legal_core.intake_preparation_contracts import DraftSuggestions

SOURCE = (
    "Вымышленный учебный пример. Услугу «консультация» оказали 4 сентября этого года. "
    "12 октября поступило сообщение о спорной цене услуги."
)


def _invoke(source, payload, calls, *, as_of_date):
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "message": {"role": "assistant", "content": json.dumps(payload)},
            "finish_reason": "stop",
        }]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            model = HermesClient(
                HermesEndpoint("http://researcher:8642", "synthetic-key",
                               model="existing-researcher", timeout_seconds=65),
                client=http,
            )
            return await IntakeUnderstanding(researcher=model).understand(
                source, as_of_date=as_of_date,
            )

    return asyncio.run(run())


@pytest.mark.parametrize("anchor", [date(2026, 12, 31), None])
def test_understanding_api_forwards_core_date_unchanged_without_adding_it_to_receipt(anchor):
    operation_id, draft_id, lease_token = uuid4(), uuid4(), uuid4()
    seen = []

    async def get_context(**kwargs):
        # The Core DTO itself is covered by independent Core contract tests.
        # A duck-typed boundary isolates Agent forwarding from DTO validation.
        return SimpleNamespace(
            operation_id=operation_id, draft_id=draft_id, draft_revision=2,
            input_sha256="a" * 64, source=SOURCE, as_of_date=anchor,
        )

    async def understand(source, *, as_of_date):
        seen.append((source, as_of_date))
        return DraftSuggestions(sourceSpans={})

    app = FastAPI()
    app.include_router(create_understanding_router(
        legal_core=SimpleNamespace(get_understanding_context=get_context),
        understanding=SimpleNamespace(understand=understand),
        require_internal_key=lambda _key: None, stage_timeout=1, wall_timeout=2,
    ))
    with TestClient(app) as client:
        response = client.post(
            f"/v1/intake-preparations/{operation_id}/understand",
            headers={"X-Agent-Internal-Key": "synthetic-internal-key",
                     "X-Telegram-User-Id": "123"},
            json={"draftId": str(draft_id), "leaseToken": str(lease_token),
                  "expectedRevision": 2, "expectedInputSha256": "a" * 64},
        )

    assert response.status_code == 200, response.text
    assert seen == [(SOURCE, anchor)]
    assert "asOfDate" not in response.json()
    assert str(lease_token) not in response.text


class _ClockDate(date):
    wall_date = date(2030, 12, 31)

    @classmethod
    def today(cls):
        return cls(cls.wall_date.year, cls.wall_date.month, cls.wall_date.day)


def test_model_date_reference_stays_bound_over_a_retry_and_wall_clock_year_change(monkeypatch):
    monkeypatch.setattr(understanding_module, "date", _ClockDate)
    anchor = _ClockDate(2026, 12, 31)
    source = "Учебный пример: вчера поступило сообщение о стоимости консультации."
    calls = []
    for wall_date in (date(2030, 12, 31), date(2031, 1, 1)):
        monkeypatch.setattr(_ClockDate, "wall_date", wall_date)
        result = _invoke(source, {"sourceSpans": {}}, calls, as_of_date=anchor)
        assert result.event_date is None

    requests = [json.loads(call["messages"][1]["content"]) for call in calls]
    assert requests == [{"source": source, "asOfDate": "2026-12-31"}] * 2
    assert all("asOfDate" in call["messages"][0]["content"] for call in calls)


def _date_suggestion(field, value):
    fields = {"eventDate": value} if field == "eventDate" else {
        "situationAreas": ["TREATMENT"], "affectedServices": ["консультация"],
        "analysisClarificationAnswers": {"answers": [{
            "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0, "value": value,
        }]},
    }
    return {**fields, "sourceSpans": {
        name: [{"start": 0, "end": len(SOURCE)}] for name in fields
    }}


@pytest.mark.parametrize("field", ["eventDate", "analysisClarificationAnswers"])
def test_future_event_or_service_date_is_rejected_against_reference_not_later_wall_clock(
    monkeypatch, field,
):
    monkeypatch.setattr(understanding_module, "date", _ClockDate)
    monkeypatch.setattr(_ClockDate, "wall_date", date(2031, 1, 1))
    candidate = _date_suggestion(field, {"date": "2027-01-01", "precision": "EXACT"})
    with pytest.raises(HermesProtocolError):
        _invoke(SOURCE, candidate, [], as_of_date=_ClockDate(2026, 12, 31))


@pytest.mark.parametrize("field", ["eventDate", "analysisClarificationAnswers"])
def test_historical_date_on_reference_day_does_not_fail_against_earlier_wall_clock(
    monkeypatch, field,
):
    monkeypatch.setattr(understanding_module, "date", _ClockDate)
    monkeypatch.setattr(_ClockDate, "wall_date", date(2025, 1, 1))
    candidate = _date_suggestion(field, {"date": "2026-12-31", "precision": "EXACT"})
    result = _invoke(SOURCE, candidate, [], as_of_date=_ClockDate(2026, 12, 31))
    assert result.source_spans


@pytest.mark.parametrize("field", ["eventDate", "analysisClarificationAnswers"])
def test_explicitly_unknown_date_retains_null_with_a_bound_reference(field):
    candidate = _date_suggestion(field, {"date": None, "precision": "UNKNOWN"})
    result = _invoke(SOURCE, candidate, [], as_of_date=date(2026, 12, 31))
    actual = result.model_dump(mode="json", by_alias=True)[field]
    if field == "analysisClarificationAnswers":
        actual = actual["answers"][0]["value"]
    assert actual == {"date": None, "precision": "UNKNOWN"}


def test_legacy_missing_reference_keeps_source_only_request_and_abstention(monkeypatch):
    monkeypatch.setattr(understanding_module, "date", _ClockDate)
    monkeypatch.setattr(_ClockDate, "wall_date", date(2031, 1, 1))
    source = "Учебный пример: вчера поступило сообщение о стоимости консультации."
    calls = []
    result = _invoke(source, {"sourceSpans": {}}, calls, as_of_date=None)
    assert json.loads(calls[0]["messages"][1]["content"]) == {"source": source}
    assert result.model_dump(mode="json", by_alias=True, exclude_none=True) == {"sourceSpans": {}}
