"""Atomic explicit consent, PostgreSQL replay and cancellation; synthetic source only."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.case_api import ApiError
from legal_core.database import owner_database_url
import legal_core.intake_preparation as preparation
from legal_core.intake_preparation_acceptance import accept_preparation
from legal_core.intake_preparation_contracts import AcceptUnderstandingRequest
from test_case_api import actor_headers, application_client
from test_intake_preparation_lifecycle import _reserve, _run, _suggestions, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]


def _proposal(actor, draft):
    claim = _run(lambda sessions: _reserve(sessions, actor, draft))
    result = _run(lambda sessions: preparation.complete_preparation(
        sessions, telegram_user_id=actor, operation_id=claim.operation_id,
        lease_token=claim.lease_token, suggestions=_suggestions(),
    ))
    request = AcceptUnderstandingRequest(
        expectedRevision=result.source.draft_revision,
        expectedInputSha256=result.source.input_sha256,
        expectedProposalSha256=result.source.proposal_sha256,
    )
    return result, request


def _accept(sessions, actor, result, request):
    return accept_preparation(
        sessions, telegram_user_id=actor, operation_id=result.operation_id, request=request,
    )


def test_concurrent_accepts_write_once_and_do_not_create_case_or_report(draft_owner):
    actor, clinic, _, draft = draft_owner
    result, request = _proposal(actor, draft)

    async def concurrent(sessions):
        return await asyncio.gather(*[_accept(sessions, actor, result, request) for _ in range(4)])

    accepted = _run(concurrent)
    assert all(item == accepted[0] for item in accepted)
    assert accepted[0].accepted_revision == draft["revision"] + 1
    assert accepted[0].draft.draft_data == {
        **draft["draftData"], "clinicActions": ["INVITED_FOR_EXAMINATION"],
    }
    replay = _run(lambda sessions: _accept(sessions, actor, result, request))
    assert replay == accepted[0]
    status = _run(lambda sessions: preparation.read_preparation(
        sessions, telegram_user_id=actor, operation_id=result.operation_id,
    ))
    assert status.state == "ACCEPTED" and status.suggestions is None
    assert status.acceptance.accepted_revision == draft["revision"] + 1
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM cases WHERE clinic_id=:id"),
                                     {"id": clinic}) == 0
            assert connection.scalar(text(
                "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                "AND action='INTAKE_PREPARATION_ACCEPTED'"
            ), {"id": result.operation_id}) == 1
    finally:
        engine.dispose()


def test_lost_response_replay_returns_current_later_edit_without_extending_retention(draft_owner):
    actor, _, _, draft = draft_owner
    result, request = _proposal(actor, draft)
    accepted = _run(lambda sessions: _accept(sessions, actor, result, request))
    with application_client() as client:
        edit = client.put(f"/v1/telegram-intake-drafts/{draft['id']}",
                          headers=actor_headers(actor, uuid4()), json={
                              "expectedRevision": accepted.accepted_revision,
                              "wizardState": "HEALTH", "draftData": {
                                  **accepted.draft.draft_data, "healthSignals": ["UNKNOWN"],
                              },
                          })
        assert edit.status_code == 200, edit.text
    replay = _run(lambda sessions: _accept(sessions, actor, result, request))
    assert replay.accepted_revision == accepted.accepted_revision
    assert replay.draft.revision == edit.json()["revision"]
    assert replay.draft.draft_data == edit.json()["draftData"]
    assert replay.draft.purge_after.isoformat() == edit.json()["purgeAfter"].replace("Z", "+00:00")


@pytest.mark.parametrize("first", ["cancel", "accept"])
def test_cancel_and_accept_never_undo_each_other(draft_owner, first):
    actor, _, _, draft = draft_owner
    result, request = _proposal(actor, draft)
    def cancel(sessions):
        return preparation.cancel_preparation(
            sessions, telegram_user_id=actor, operation_id=result.operation_id,
        )

    def accept(sessions):
        return _accept(sessions, actor, result, request)
    _run(cancel if first == "cancel" else accept)
    with pytest.raises(ApiError) as error:
        _run(accept if first == "cancel" else cancel)
    assert error.value.status_code == 409
    if first == "accept":
        assert _run(accept).accepted_revision == draft["revision"] + 1


@pytest.mark.parametrize("field", ["expectedRevision", "expectedInputSha256",
                                    "expectedProposalSha256"])
def test_changed_acceptance_request_conflicts_before_and_after_commit(draft_owner, field):
    actor, _, _, draft = draft_owner
    result, request = _proposal(actor, draft)
    changed = request.model_dump(by_alias=True)
    changed[field] = request.expected_revision + 1 if field == "expectedRevision" else "0" * 64
    changed = AcceptUnderstandingRequest.model_validate(changed)
    for accepted in (False, True):
        if accepted:
            _run(lambda sessions: _accept(sessions, actor, result, request))
        with pytest.raises(ApiError) as error:
            _run(lambda sessions: _accept(sessions, actor, result, changed))
        assert error.value.status_code == 409


@pytest.mark.parametrize("mutation", ["expired", "receipt", "scope"])
def test_accepted_receipt_is_not_authority_without_live_ownership_and_audit(draft_owner, mutation):
    actor, _, _, draft = draft_owner
    result, request = _proposal(actor, draft)
    _run(lambda sessions: _accept(sessions, actor, result, request))
    statements = {
        "expired": "UPDATE telegram_intake_drafts SET purge_after=now()-interval '1 second' "
                   "WHERE id=:draft",
        "receipt": "UPDATE idempotency_records SET response_json=jsonb_set(response_json, "
                   "'{acceptance,acceptedRevision}', '999'::jsonb) WHERE id=:operation",
        "scope": "UPDATE idempotency_records SET request_sha256=repeat('0',64) WHERE id=:operation",
    }
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(statements[mutation]),
                               {"draft": draft["id"], "operation": result.operation_id})
        with pytest.raises(ApiError) as error:
            _run(lambda sessions: _accept(sessions, actor, result, request))
        assert error.value.status_code in {409, 410}
    finally:
        engine.dispose()
