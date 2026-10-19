"""Authenticated opt-in transport, not producer activation or legal approval."""

import asyncio
from uuid import uuid4

import pytest

from agent_orchestrator.legal_core_client import LegalCoreProtocolError
from legal_core.analysis_contracts import PreparationResult
from test_main import CASE_ID, IDEMPOTENCY_KEY, INTERNAL_KEY, _context
from test_main import _client as _legacy_client
from test_preparation_transport import preparation_wire


def _client():
    client, core, reasoning = _legacy_client()

    async def prepare(**kwargs):
        return await core.get_analysis_context(case_id=kwargs["case_id"],
                                               telegram_user_id=kwargs["telegram_user_id"])

    core.prepare_analysis_context = prepare
    return client, core, reasoning


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


@pytest.mark.parametrize("kind", ["LIMITED", "HUMAN_REVIEW", "NEEDS_FACTS"])
@pytest.mark.parametrize("retained", [False, True])
def test_core_preparation_or_retained_result_skips_models_and_submission(kind, retained):
    client, core, reasoning = _client()
    wire = preparation_wire(_context(), kind)
    if not retained:
        wire["source"]["context"] = None
    elif kind == "LIMITED":
        wire["reasonCode"] = "EVIDENCE_NOT_VERIFIED"
    received, token = [], uuid4()

    async def prepare(**kwargs):
        received.append(kwargs)
        return PreparationResult.model_validate(wire)

    async def forbidden_context(**kwargs):
        pytest.fail("V2 must use the Core preparation gate, not legacy context")

    core.prepare_analysis_context = prepare
    core.get_analysis_context = forbidden_context
    response = client.post(f"/v1/cases/{CASE_ID}/analyze", headers=headers() | {
        "X-Analysis-Job-Id": str(IDEMPOTENCY_KEY), "X-Analysis-Job-Token": str(token),
    })
    assert response.status_code == 200, response.text
    assert response.json() == wire
    assert reasoning.calls == core.submit_calls == 0
    assert received[0]["idempotency_key"] == received[0]["job_id"] == IDEMPOTENCY_KEY
    assert received[0]["job_token"] == token


@pytest.mark.parametrize(("status", "code"), [(403, "ACTOR_NOT_AUTHORIZED"),
                                             (409, "RISK_POLICY_NOT_READY"),
                                             (503, "LEGAL_CORE_UNAVAILABLE")])
def test_precontext_errors_are_not_relabelled_as_missing_facts(status, code):
    from agent_orchestrator.legal_core_client import LegalCoreError

    client, core, reasoning = _client()

    async def reject(**kwargs):
        raise LegalCoreError(code, "Synthetic bounded refusal", status_code=status)

    core.prepare_analysis_context = reject
    response = client.post(f"/v1/cases/{CASE_ID}/analyze", headers=headers())
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert reasoning.calls == core.submit_calls == 0
