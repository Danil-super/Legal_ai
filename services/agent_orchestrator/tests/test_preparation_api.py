"""Authenticated opt-in transport, not producer activation or legal approval."""

import asyncio
from uuid import uuid4

import pytest

from agent_orchestrator.legal_core_client import LegalCoreProtocolError
from legal_core.analysis_contracts import PreparationResult
from test_main import CASE_ID, IDEMPOTENCY_KEY, INTERNAL_KEY, _client, _context
from test_preparation_transport import preparation_wire


def headers():
    return {"X-Agent-Internal-Key": INTERNAL_KEY, "X-Telegram-User-Id": "123",
            "Idempotency-Key": str(IDEMPOTENCY_KEY), "X-Analysis-Result-Version": "2"}


@pytest.mark.parametrize("status", ["NEEDS_FACTS", "LIMITED", "HUMAN_REVIEW"])
def test_opt_in_route_preserves_core_result_and_job_fence_without_fake_report(status):
    client, core, reasoning = _client()
    result = preparation_wire(_context(), status)
    received = []
    token = uuid4()

    async def submit(**kwargs):
        received.append(kwargs)
        return PreparationResult.model_validate(result)
    core.submit_reasoning = submit
    response = client.post(f"/v1/cases/{CASE_ID}/analyze", headers={
        **headers(), "X-Analysis-Job-Id": str(IDEMPOTENCY_KEY), "X-Analysis-Job-Token": str(token),
    })
    assert response.status_code == 200, response.text
    assert PreparationResult.model_validate(response.json()) == (
        PreparationResult.model_validate(result)
    )
    assert reasoning.calls == 1
    assert received[0]["result_version"] == 2
    assert received[0]["job_id"] == IDEMPOTENCY_KEY
    assert received[0]["job_token"] == token
    assert "report" not in response.json()


@pytest.mark.parametrize("version", ["0", "3", "2.0", "true", "invalid"])
def test_unknown_transport_version_stops_before_context_and_models(version):
    client, core, reasoning = _client()
    response = client.post(f"/v1/cases/{CASE_ID}/analyze",
                           headers=headers() | {"X-Analysis-Result-Version": version})
    assert response.status_code == 422
    assert core.context_calls == core.submit_calls == reasoning.calls == 0


@pytest.mark.parametrize("failure", ["auth", "lease", "protocol", "timeout"])
def test_v2_does_not_bypass_auth_lease_protocol_or_existing_deadline(failure, monkeypatch):
    client, core, reasoning = _client()
    request_headers = headers()
    if failure == "auth":
        request_headers["X-Agent-Internal-Key"] = "x" * 32
    elif failure == "lease":
        request_headers["X-Analysis-Job-Id"] = str(IDEMPOTENCY_KEY)
    elif failure == "protocol":
        async def invalid(**kwargs):
            raise LegalCoreProtocolError("UNTRUSTED_SYNTHETIC_DETAILS")
        core.submit_reasoning = invalid
    else:
        monkeypatch.setattr("agent_orchestrator.main.ANALYSIS_WALL_TIMEOUT_SECONDS", 0.02)

        async def blocked(projection):
            await asyncio.Event().wait()
        reasoning.reason = blocked
    response = client.post(f"/v1/cases/{CASE_ID}/analyze", headers=request_headers)
    expected = {"auth": 403, "lease": 409, "protocol": 503, "timeout": 503}[failure]
    assert response.status_code == expected
    assert "UNTRUSTED" not in response.text
    assert "report" not in response.json()


def test_opt_in_route_never_silently_downgrades_to_a_legacy_result():
    from test_main import _submission_response
    client, core, reasoning = _client()

    async def wrong_version(**kwargs):
        return _submission_response()
    core.submit_reasoning = wrong_version
    response = client.post(f"/v1/cases/{CASE_ID}/analyze", headers=headers())
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "ANALYSIS_PROVIDER_UNAVAILABLE"}}
    assert reasoning.calls == 1
