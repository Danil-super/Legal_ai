"""Durable cancellation fences late work without deleting the owner's saved draft."""

import os
from uuid import uuid4

import pytest

from legal_core.case_api import ApiError
import legal_core.intake_preparation as preparation
from test_case_api import actor_headers, application_client, seed_admin
from test_intake_preparation_lifecycle import _reserve, _run, _suggestions, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]


@pytest.mark.parametrize("completed", [False, True])
def test_cancel_survives_restart_refuses_late_completion_and_preserves_draft(draft_owner, completed):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    if completed:
        _run(lambda sessions: preparation.complete_preparation(
            sessions, telegram_user_id=actor, operation_id=lease.operation_id,
            lease_token=lease.lease_token, suggestions=_suggestions(),
        ))
    cancelled = _run(lambda sessions: preparation.cancel_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
    ))
    resumed = _run(lambda sessions: preparation.read_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
    ))
    assert cancelled.state == resumed.state == "MANUAL_CONTINUATION"
    assert resumed.suggestions is None and resumed.lease_token is None
    retry = _run(lambda sessions: _reserve(sessions, actor, draft))
    assert retry == resumed
    assert _run(lambda sessions: preparation.cancel_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
    )) == resumed
    with pytest.raises(ApiError) as error:
        _run(lambda sessions: preparation.complete_preparation(
            sessions, telegram_user_id=actor, operation_id=lease.operation_id,
            lease_token=lease.lease_token, suggestions=_suggestions(),
        ))
    assert error.value.code == "PREPARATION_LEASE_EXPIRED"
    with application_client() as client:
        actual = client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                            headers=actor_headers(actor)).json()
    assert actual["draftData"] == draft["draftData"]
    assert actual["revision"] == draft["revision"] and actual["status"] == "DRAFT"


def test_cancellation_is_available_during_analysis_safe_stop(draft_owner, monkeypatch):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    assert _run(lambda sessions: preparation.cancel_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
    )).state == "MANUAL_CONTINUATION"


def test_foreign_actor_cannot_cancel_the_owners_operation(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    other = 20_800_000_000 + uuid4().int % 100_000_000
    seed_admin(other)
    with pytest.raises(ApiError) as error:
        _run(lambda sessions: preparation.cancel_preparation(
            sessions, telegram_user_id=other, operation_id=lease.operation_id,
        ))
    assert error.value.status_code == 404
    assert _run(lambda sessions: preparation.read_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
    )).state == "PENDING"
