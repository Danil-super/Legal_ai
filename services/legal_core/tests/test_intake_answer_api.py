"""Protected public answer routes expose no private source or dispatch metadata."""

import os
from uuid import uuid4

import pytest

from test_case_api import actor_headers, application_client, seed_admin
from test_intake_preparation_lifecycle import draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]


def test_answer_reservation_without_runner_is_manual_and_public_shape_is_closed(draft_owner):
    actor, _, _, draft = draft_owner
    path = f"/v1/telegram-intake-drafts/{draft['id']}/answer-interpretations"
    with application_client() as client:
        empty = client.get(path, headers=actor_headers(actor))
        assert empty.status_code == 200, empty.text
        assert empty.json() == {"operation": None}
        reserved = client.post(path, headers=actor_headers(actor), json={
            "expectedRevision": draft["revision"], "answer": "Описание фактов без имён.",
        })
        assert reserved.status_code == 202, reserved.text
        status = reserved.json()
        assert set(status) == {"operationId", "state", "question", "interpretation",
                               "source", "acceptance"}
        assert status["state"] == "MANUAL_CONTINUATION"
        assert all(status[key] is None for key in ("question", "interpretation", "source",
                                                  "acceptance"))
        operation = f"/v1/intake-answer-preparations/{status['operationId']}"
        assert client.get(operation, headers=actor_headers(actor)).json() == status
        assert client.delete(operation, headers=actor_headers(actor)).json() == status
        assert client.post(operation + "/context", headers=actor_headers(actor), json={
            "expectedRevision": draft["revision"], "leaseToken": str(uuid4()),
            "expectedInputSha256": "a" * 64,
        }).status_code == 403
        other = 93_000_000_000 + uuid4().int % 10_000_000
        seed_admin(other)
        for method in (client.get, client.delete):
            assert method(operation, headers=actor_headers(other)).status_code == 404
        assert client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                          headers=actor_headers(actor)).json() == draft


@pytest.mark.parametrize("extra", ["question", "clinicId", "interpretation", "targetField"])
def test_caller_cannot_choose_question_or_result(draft_owner, extra):
    actor, _, _, draft = draft_owner
    with application_client() as client:
        response = client.post(
            f"/v1/telegram-intake-drafts/{draft['id']}/answer-interpretations",
            headers=actor_headers(actor), json={"expectedRevision": draft["revision"],
                                               "answer": "Новый ответ без имён.", extra: "forged"},
        )
    assert response.status_code == 422
    assert "Новый ответ" not in response.text and "forged" not in response.text


@pytest.mark.parametrize("state", ["MATERIALS", "SUMMARY", "V2_CONFIRM"])
def test_nonfactual_checkpoint_discovery_does_not_block_navigation(draft_owner, state):
    actor, _, _, draft = draft_owner
    with application_client() as client:
        saved = client.put(f"/v1/telegram-intake-drafts/{draft['id']}",
                           headers=actor_headers(actor, uuid4()), json={
                               "expectedRevision": draft["revision"], "wizardState": state,
                               "draftData": draft["draftData"],
                           })
        assert saved.status_code == 200, saved.text
        response = client.get(
            f"/v1/telegram-intake-drafts/{draft['id']}/answer-interpretations",
            headers=actor_headers(actor),
        )
    assert response.status_code == 200, response.text
    assert response.json() == {"operation": None}
