"""Protected understanding must resolve Core metadata before making one model call."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from agent_orchestrator.hermes_client import HermesProtocolError, HermesUnavailable
from agent_orchestrator.intake_understanding import IntakeUnderstanding
from agent_orchestrator.legal_core_client import LegalCoreError, LegalCoreProtocolError
from agent_orchestrator.main import ServiceDependencies, ServiceSettings, create_app
from legal_core.intake_preparation_contracts import IntakeUnderstandingContext

KEY = "synthetic-understanding-key-1234567890123456"
SOURCE = "Клиника пригласила на осмотр. Пациент сообщает о сколе коронки."


def _client(*, failure=None, timeout=65):
    operation, draft, token = uuid4(), uuid4(), uuid4()
    calls = []

    async def context(**kwargs):
        calls.append(("context", kwargs))
        if failure == "context":
            raise LegalCoreError("UNTRUSTED_CONTEXT_DETAILS", "RAW_SECRET", status_code=409)
        if failure == "context_protocol":
            raise LegalCoreProtocolError("RAW_SECRET")
        return IntakeUnderstandingContext(
            operationId=operation, draftId=draft, draftRevision=2, inputSha256="a" * 64,
            operationKind="UNDERSTAND_INTAKE", source=SOURCE,
        )

    async def complete_json(**kwargs):
        calls.append(("model", kwargs))
        if failure == "unavailable":
            raise HermesUnavailable("RAW_SECRET")
        if failure == "protocol":
            raise HermesProtocolError("RAW_SECRET")
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "invalid":
            return {"confirmed": True, "source": "RAW_SECRET"}
        return {"clinicActions": ["INVITED_FOR_EXAMINATION"],
                "sourceSpans": {"clinicActions": [{"start": 0, "end": 26}]}}

    settings = ServiceSettings(KEY, "http://core:8000", "http://researcher:8642", "r", "researcher",
                               "http://reviewer:8642", "v", "reviewer")
    dependencies = ServiceDependencies(
        legal_core=SimpleNamespace(get_understanding_context=context),
        reasoning=SimpleNamespace(),
        understanding=IntakeUnderstanding(researcher=SimpleNamespace(complete_json=complete_json)),
    )
    from unittest.mock import patch
    with patch("agent_orchestrator.main.HERMES_STAGE_TIMEOUT_SECONDS", timeout):
        client = TestClient(create_app(settings=settings, dependencies=dependencies))
    return client, calls, f"/v1/intake-preparations/{operation}/understand", {
        "draftId": str(draft), "leaseToken": str(token), "expectedRevision": 2,
        "expectedInputSha256": "a" * 64,
    }


def _headers():
    return {"X-Agent-Internal-Key": KEY, "X-Telegram-User-Id": "123"}


def test_one_authenticated_metadata_invocation_resolves_core_before_model():
    client, calls, path, body = _client()
    response = client.post(path, headers=_headers(), json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["draftId"] == body["draftId"] and result["draftRevision"] == 2
    assert result["inputSha256"] == body["expectedInputSha256"]
    assert result["suggestions"]["clinicActions"] == ["INVITED_FOR_EXAMINATION"]
    assert [call[0] for call in calls] == ["context", "model"]
    assert calls[0][1]["telegram_user_id"] == 123
    assert json.loads(calls[1][1]["user"]) == {"source": SOURCE}
    assert body["leaseToken"] not in str(calls[1][1])
    assert SOURCE not in response.text and body["leaseToken"] not in response.text
    assert "report" not in result and "confirmed" not in result


@pytest.mark.parametrize("change", [
    {"source": "RAW_SECRET"}, {"clinicId": "RAW_SECRET"}, {"expectedRevision": True},
    {"leaseToken": "RAW_SECRET"}, {"expectedInputSha256": "RAW_SECRET"},
])
def test_bad_input_is_rejected_without_raw_validation_echo_or_model_call(change):
    client, calls, path, body = _client()
    response = client.post(path, headers=_headers(), json=body | change)
    assert response.status_code == 422
    assert "RAW_SECRET" not in response.text and body["leaseToken"] not in response.text
    assert calls == []


@pytest.mark.parametrize("key", [None, "wrong-key-123456789012345678901234567890"])
def test_authentication_precedes_context_and_model(key):
    client, calls, path, body = _client()
    headers = _headers()
    if key is None:
        del headers["X-Agent-Internal-Key"]
    else:
        headers["X-Agent-Internal-Key"] = key
    response = client.post(path, headers=headers, json=body)
    assert response.status_code in {403, 422}
    assert calls == []


@pytest.mark.parametrize("failure,status,code", [
    ("context", 409, "UNDERSTANDING_CONTEXT_REFUSED"),
    ("context_protocol", 503, "UNDERSTANDING_CONTEXT_UNAVAILABLE"),
    ("unavailable", 503, "MODEL_UNAVAILABLE"), ("protocol", 502, "MODEL_PROTOCOL_INVALID"),
    ("invalid", 502, "MODEL_PROTOCOL_INVALID"), ("timeout", 503, "MODEL_TIMEOUT"),
])
def test_failures_are_bounded_and_do_not_become_confirmed_facts(failure, status, code):
    client, calls, path, body = _client(
        failure=failure, timeout=0.01 if failure == "timeout" else 65,
    )
    response = client.post(path, headers=_headers(), json=body)
    assert response.status_code == status, response.text
    assert response.json() == {"detail": {"code": code}}
    assert "RAW_SECRET" not in response.text and SOURCE not in response.text
    assert len(calls) == (1 if failure.startswith("context") else 2)
