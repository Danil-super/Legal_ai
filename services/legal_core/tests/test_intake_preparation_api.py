"""Internal metadata-only context transport, with real owned PostgreSQL drafts."""

import os
from uuid import uuid4

import pytest

from test_case_api import actor_headers, application_client
from test_intake_preparation_context import _binding
from test_intake_preparation_lifecycle import SOURCE, _reserve, _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]
KEY = "synthetic-internal-context-key-1234567890123456"


def _request(actor, lease):
    intent = _binding(lease)
    return (
        f"/v1/intake-preparations/{lease.operation_id}/context",
        {**actor_headers(actor), "X-Agent-Internal-Key": KEY},
        {"leaseToken": str(lease.lease_token), "expectedRevision": intent["draftRevision"],
         "expectedInputSha256": intent["inputSha256"]},
    )


def test_internal_context_endpoint_returns_only_exact_owned_model_input(draft_owner, monkeypatch):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", KEY)
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    path, headers, body = _request(actor, lease)
    with application_client() as client:
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "operationId": str(lease.operation_id), "draftId": draft["id"],
            "draftRevision": draft["revision"], "inputSha256": body["expectedInputSha256"],
            "operationKind": "UNDERSTAND_INTAKE", "source": SOURCE,
        }
        assert str(lease.lease_token) not in response.text
        actual = client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                            headers=actor_headers(actor)).json()
        assert actual["revision"] == draft["revision"]
        assert actual["draftData"] == draft["draftData"]


@pytest.mark.parametrize("key", [None, "wrong-internal-key-12345678901234567890"])
def test_service_key_is_required_even_with_valid_actor_and_lease(draft_owner, monkeypatch, key):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", KEY)
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    path, headers, body = _request(actor, lease)
    if key is None:
        del headers["X-Agent-Internal-Key"]
    else:
        headers["X-Agent-Internal-Key"] = key
    with application_client() as client:
        response = client.post(path, headers=headers, json=body)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INTERNAL_ACCESS_REQUIRED"
    assert SOURCE not in response.text and str(lease.lease_token) not in response.text


@pytest.mark.parametrize("field,value", [
    ("expectedRevision", True), ("expectedRevision", "2"), ("expectedRevision", 0),
    ("expectedInputSha256", "invalid-hash"), ("leaseToken", "invalid-lease"),
    ("source", "Untrusted source must not be accepted or reflected"),
    ("clinicId", "Untrusted tenant context"),
])
def test_context_request_is_metadata_only_and_validation_does_not_echo_inputs(
    draft_owner, monkeypatch, field, value,
):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", KEY)
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    path, headers, body = _request(actor, lease)
    body[field] = value
    with application_client() as client:
        response = client.post(path, headers=headers, json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert str(lease.lease_token) not in response.text and SOURCE not in response.text
    assert all(set(detail) == {"location", "type"}
               for detail in response.json()["error"]["details"])


def test_valid_internal_key_does_not_replace_current_lease_or_actor(draft_owner, monkeypatch):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", KEY)
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    path, headers, body = _request(actor, lease)
    body["leaseToken"] = str(uuid4())
    with application_client() as client:
        response = client.post(path, headers=headers, json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PREPARATION_LEASE_EXPIRED"
    assert SOURCE not in response.text
