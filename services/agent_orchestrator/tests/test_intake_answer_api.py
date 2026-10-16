"""Protected metadata-only answer analysis resolves current Core context first."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from agent_orchestrator.hermes_client import HermesProtocolError, HermesUnavailable
from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding
from agent_orchestrator.legal_core_client import LegalCoreError, LegalCoreProtocolError
from agent_orchestrator.main import (
    ServiceDependencies,
    ServiceSettings,
    build_dependencies,
    create_app,
)
from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_answer_wire import AnswerContext

KEY = "synthetic-answer-key-12345678901234567890"
ANSWER = "Это письменная претензия"


def client_scenario(
    *, failure=None, timeout=65, wall_timeout=115, context_patch=None, configured=True,
):
    operation, draft, token = uuid4(), uuid4(), uuid4()
    calls = []
    question = derive_answer_question({"intakeVersion": 2}, "INCOMING")
    context_body = {"operationId": str(operation), "draftId": str(draft), "draftRevision": 2,
                    "inputSha256": "a" * 64,
                    "answerSha256": hashlib.sha256(ANSWER.encode()).hexdigest(),
                    "questionSha256": question.question_sha256,
                    "question": question.model_dump(mode="json", by_alias=True),
                    "answer": ANSWER, "confirmedContext": {"intakeVersion": 2},
                    "asOfDate": "2026-10-16"}
    context_body.update(context_patch or {})

    async def context(**kwargs):
        calls.append(("context", kwargs))
        if failure == "context":
            raise LegalCoreError("SYNTHETIC_PRIVATE_DETAIL", "SYNTHETIC_PRIVATE_DETAIL",
                                 status_code=409)
        if failure == "context_protocol":
            raise LegalCoreProtocolError("SYNTHETIC_PRIVATE_DETAIL")
        if failure == "context_timeout":
            await asyncio.Event().wait()
        return AnswerContext.model_validate(context_body)

    async def complete_json(**kwargs):
        calls.append(("model", kwargs))
        if failure == "unavailable":
            raise HermesUnavailable("SYNTHETIC_PRIVATE_DETAIL")
        if failure == "protocol":
            raise HermesProtocolError("SYNTHETIC_PRIVATE_DETAIL")
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "invalid":
            return {"confirmed": True, "source": "SYNTHETIC_PRIVATE_DETAIL"}
        if failure == "unresolved":
            return {"status": "UNRESOLVED", "value": None,
                    "sourceSpans": [], "reasonCode": "AMBIGUOUS"}
        return {"status": "PROPOSED", "value": "FORMAL_DOCUMENT",
                "sourceSpans": [{"start": 0, "end": len(ANSWER)}]}

    settings = ServiceSettings(KEY, "http://core:8000", "http://researcher:8642", "r", "researcher",
                               "http://reviewer:8642", "v", "reviewer")
    dependencies = ServiceDependencies(
        legal_core=SimpleNamespace(get_answer_context=context), reasoning=SimpleNamespace(),
        answer_understanding=AnswerUnderstanding(researcher=SimpleNamespace(
            complete_json=complete_json)) if configured else None,
    )
    with (patch("agent_orchestrator.main.HERMES_STAGE_TIMEOUT_SECONDS", timeout),
          patch("agent_orchestrator.main.ANALYSIS_WALL_TIMEOUT_SECONDS", wall_timeout)):
        client = TestClient(create_app(settings=settings, dependencies=dependencies))
    body = {"draftId": str(draft), "leaseToken": str(token), "expectedRevision": 2,
            "expectedInputSha256": "a" * 64}
    return client, calls, f"/v1/intake-answer-preparations/{operation}/interpret", body


def headers():
    return {"X-Agent-Internal-Key": KEY, "X-Telegram-User-Id": "123"}


def test_metadata_invocation_fetches_core_context_before_one_existing_model_call():
    client, calls, path, body = client_scenario()
    response = client.post(path, headers=headers(), json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["draftId"] == body["draftId"] and result["draftRevision"] == 2
    assert result["inputSha256"] == body["expectedInputSha256"]
    assert result["answerSha256"] == hashlib.sha256(ANSWER.encode()).hexdigest()
    assert result["interpretation"]["value"] == "FORMAL_DOCUMENT"
    assert [item[0] for item in calls] == ["context", "model"]
    assert calls[0][1]["telegram_user_id"] == 123
    assert json.loads(calls[1][1]["user"])["answer"] == ANSWER
    assert body["leaseToken"] not in str(calls[1][1])
    assert ANSWER not in response.text and body["leaseToken"] not in response.text


@pytest.mark.parametrize("change", [
    {"answer": "SYNTHETIC_PRIVATE_DETAIL"}, {"question": "SYNTHETIC_PRIVATE_DETAIL"},
    {"clinicId": "SYNTHETIC_PRIVATE_DETAIL"}, {"expectedRevision": True},
    {"leaseToken": "SYNTHETIC_PRIVATE_DETAIL"}, {"expectedInputSha256": "SYNTHETIC_PRIVATE_DETAIL"},
])
def test_arbitrary_source_or_metadata_is_rejected_without_validation_echo(change):
    client, calls, path, body = client_scenario()
    response = client.post(path, headers=headers(), json=body | change)
    assert response.status_code == 422 and calls == []
    assert "SYNTHETIC_PRIVATE_DETAIL" not in response.text
    assert body["leaseToken"] not in response.text


@pytest.mark.parametrize("change", [
    {"X-Agent-Internal-Key": "wrong-key-123456789012345678901234567890"},
    {"X-Agent-Internal-Key": None}, {"X-Telegram-User-Id": None},
    {"X-Telegram-User-Id": "0"}, {"X-Telegram-User-Id": "-1"},
])
def test_key_and_positive_actor_are_required_before_context_or_model(change):
    client, calls, path, body = client_scenario()
    changed = headers() | change
    response = client.post(path, headers={key: value for key, value in changed.items()
                                         if value is not None}, json=body)
    assert response.status_code in {403, 422} and calls == []


@pytest.mark.parametrize("failure,status,code", [
    ("context", 409, "ANSWER_CONTEXT_REFUSED"),
    ("context_protocol", 503, "ANSWER_CONTEXT_UNAVAILABLE"),
    ("unavailable", 503, "MODEL_UNAVAILABLE"), ("protocol", 502, "MODEL_PROTOCOL_INVALID"),
    ("invalid", 502, "MODEL_PROTOCOL_INVALID"), ("timeout", 503, "MODEL_TIMEOUT"),
])
def test_failures_keep_fixed_codes_and_never_log_private_input(failure, status, code, caplog):
    client, calls, path, body = client_scenario(
        failure=failure, timeout=0.01 if failure == "timeout" else 65,
    )
    response = client.post(path, headers=headers(), json=body)
    assert response.status_code == status and response.json() == {"detail": {"code": code}}
    assert "SYNTHETIC_PRIVATE_DETAIL" not in response.text + caplog.text
    assert ANSWER not in response.text + caplog.text
    assert len(calls) == (1 if failure.startswith("context") else 2)


@pytest.mark.parametrize("change", [
    {"operationId": str(uuid4())}, {"draftId": str(uuid4())}, {"draftRevision": 3},
    {"inputSha256": "b" * 64}, {"answerSha256": "b" * 64}, {"questionSha256": "b" * 64},
])
def test_mismatched_context_does_not_dispatch_model(change):
    client, calls, path, body = client_scenario(context_patch=change)
    response = client.post(path, headers=headers(), json=body)
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "ANSWER_CONTEXT_UNAVAILABLE"}}
    assert [item[0] for item in calls] == ["context"]


def test_absent_optional_dependency_is_fail_closed_and_preserves_legacy_app_construction():
    client, calls, path, body = client_scenario(configured=False)
    response = client.post(path, headers=headers(), json=body)
    assert response.status_code == 503 and calls == []
    assert response.json() == {"detail": {"code": "MODEL_UNAVAILABLE"}}


def test_fixed_total_wall_bounds_context_fetch_before_any_model_call():
    client, calls, path, body = client_scenario(failure="context_timeout", wall_timeout=0.01)
    response = client.post(path, headers=headers(), json=body)
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "MODEL_TIMEOUT"}}
    assert [item[0] for item in calls] == ["context"]


def test_identifying_source_is_refused_before_model_without_exposing_source():
    source = "patient@example.invalid"
    client, calls, path, body = client_scenario(context_patch={
        "answer": source, "answerSha256": hashlib.sha256(source.encode()).hexdigest(),
    })
    response = client.post(path, headers=headers(), json=body)
    assert response.status_code == 502 and source not in response.text
    assert response.json() == {"detail": {"code": "MODEL_PROTOCOL_INVALID"}}
    assert [item[0] for item in calls] == ["context"]


def test_unresolved_is_a_bound_result_not_unknown_or_confirmation():
    client, calls, path, body = client_scenario(failure="unresolved")
    response = client.post(path, headers=headers(), json=body)
    assert response.status_code == 200
    assert response.json()["interpretation"] == {
        "status": "UNRESOLVED", "value": None, "sourceSpans": [], "reasonCode": "AMBIGUOUS",
    }
    assert [item[0] for item in calls] == ["context", "model"]


def test_dependency_composition_reuses_one_configured_researcher_without_new_profile_or_budget():
    settings = ServiceSettings(KEY, "http://core:8000", "http://researcher:8642", "r", "researcher",
                               "http://reviewer:8642", "v", "reviewer")
    dependencies = build_dependencies(settings)
    researcher = dependencies.reasoning._researcher
    assert dependencies.understanding._researcher is researcher
    assert dependencies.answer_understanding._researcher is researcher
    assert researcher.endpoint.model == "researcher"
    assert researcher.endpoint.timeout_seconds == 65 and researcher.endpoint.stream_response is True
