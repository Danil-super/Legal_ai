"""Consent commits only a current displayed factual answer, with immutable replay."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.case_api import ApiError, _audit
from legal_core.database import owner_database_url
from legal_core.intake_answer_acceptance import accept_answer
from legal_core.intake_answer_contracts import AnswerInterpretation
from legal_core.intake_answer_operations import (
    cancel_answer,
    proposal_metadata,
    read_answer,
    reserve_answer,
    stored_answer,
)
from legal_core.intake_preparation import RESOURCE_TYPE, _locked_owned_operation
from legal_core.intake_preparation_contracts import AcceptUnderstandingRequest
from test_case_api import actor_headers, application_client
from test_intake_preparation_lifecycle import _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]
ANSWER = "Пациент сообщил о сколе коронки после лечения."


def proposed(draft_owner, *, unresolved=False):
    actor, _, _, draft = draft_owner
    claim = _run(lambda sessions: reserve_answer(
        sessions, telegram_user_id=actor, draft_id=draft["id"],
        expected_revision=draft["revision"], answer=ANSWER,
    ))

    async def publish(sessions):
        async with sessions() as session:
            owner, saved, record = await _locked_owned_operation(
                session, actor, claim.response.operation_id,
            )
            bound = stored_answer(owner, saved, record)
            bound.interpretation = AnswerInterpretation.model_validate({
                "status": "UNRESOLVED" if unresolved else "PROPOSED",
                "value": None if unresolved else ANSWER,
                "sourceSpans": [] if unresolved else [{"start": 0, "end": len(ANSWER)}],
                "reasonCode": "AMBIGUOUS" if unresolved else None,
            })
            record.response_json = bound.model_dump(mode="json", by_alias=True)
            record.state, record.lease_token, record.lease_until = "SUCCEEDED", None, None
            session.add(_audit(actor=owner, action="INTAKE_ANSWER_PROPOSED",
                               resource_type=RESOURCE_TYPE, resource_id=record.id,
                               metadata=proposal_metadata(bound)))
            await session.commit()

    _run(publish)
    result = _run(lambda sessions: read_answer(
        sessions, telegram_user_id=actor, operation_id=claim.response.operation_id,
    )).response
    return result


def consent(status):
    return AcceptUnderstandingRequest(
        expectedRevision=status.source.draft_revision,
        expectedInputSha256=status.source.input_sha256,
        expectedProposalSha256=status.source.proposal_sha256,
    )


def test_concurrent_acceptance_once_and_replay_returns_current_later_draft(draft_owner):
    actor, _, _, draft = draft_owner
    status = proposed(draft_owner)
    request = consent(status)

    async def race(sessions):
        return await asyncio.gather(*[accept_answer(
            sessions, telegram_user_id=actor, operation_id=status.operation_id, request=request,
        ) for _ in range(3)])

    accepted = _run(race)
    assert {item.accepted_revision for item in accepted} == {draft["revision"] + 1}
    assert all(item.draft.draft_data["eventSummary"] == ANSWER for item in accepted)
    with application_client() as client:
        later = client.put(f"/v1/telegram-intake-drafts/{draft['id']}",
                           headers=actor_headers(actor, uuid4()), json={
                               "expectedRevision": accepted[0].accepted_revision,
                               "wizardState": "EVENT_DATE",
                               "draftData": {**draft["draftData"], "eventSummary": ANSWER},
                           })
        assert later.status_code == 200, later.text
        later = later.json()
    replay = _run(lambda sessions: accept_answer(
        sessions, telegram_user_id=actor, operation_id=status.operation_id, request=request,
    ))
    assert replay.draft.model_dump(mode="json", by_alias=True) == later
    assert replay.accepted_revision == accepted[0].accepted_revision
    reread = _run(lambda sessions: read_answer(
        sessions, telegram_user_id=actor, operation_id=status.operation_id,
    )).response
    assert reread.state == "ACCEPTED" and reread.question is None
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                "AND action='INTAKE_ANSWER_ACCEPTED'"
            ), {"id": status.operation_id}) == 1
            assert connection.scalar(text(
                "SELECT count(*) FROM cases WHERE clinic_id=:id"
            ), {"id": draft_owner[1]}) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("field", ["expectedRevision", "expectedInputSha256",
                                  "expectedProposalSha256"])
def test_changed_display_selector_does_not_confirm_answer(draft_owner, field):
    status = proposed(draft_owner)
    changed = consent(status).model_dump(by_alias=True)
    changed[field] = changed[field] + 1 if field == "expectedRevision" else "f" * 64
    with pytest.raises(ApiError) as caught:
        _run(lambda sessions: accept_answer(
            sessions, telegram_user_id=draft_owner[0], operation_id=status.operation_id,
            request=AcceptUnderstandingRequest.model_validate(changed),
        ))
    assert caught.value.status_code == 409


def test_unresolved_answer_cannot_be_confirmed(draft_owner):
    status = proposed(draft_owner, unresolved=True)
    assert status.state == "UNRESOLVED"
    with pytest.raises(ApiError):
        _run(lambda sessions: accept_answer(
            sessions, telegram_user_id=draft_owner[0], operation_id=status.operation_id,
            request=consent(status),
        ))


@pytest.mark.parametrize("change", ["proposal", "receipt", "accepted_audit", "proposal_audit"])
def test_mutated_cache_or_immutable_audit_is_rejected(draft_owner, change):
    status = proposed(draft_owner)
    if change in {"receipt", "accepted_audit"}:
        _run(lambda sessions: accept_answer(
            sessions, telegram_user_id=draft_owner[0], operation_id=status.operation_id,
            request=consent(status),
        ))
    statements = {
        "proposal": "UPDATE idempotency_records SET response_json=jsonb_set(response_json, "
        "'{interpretation,value}', '\"Другое описание обстоятельств.\"'::jsonb) WHERE id=:id",
        "receipt": "UPDATE idempotency_records SET response_json=jsonb_set(response_json, "
        "'{acceptance,outputSha256}', '\"ffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffff\"'::jsonb) WHERE id=:id",
        "accepted_audit": "UPDATE audit_events SET metadata_json='{}'::jsonb "
        "WHERE resource_id=:id AND action='INTAKE_ANSWER_ACCEPTED'",
        "proposal_audit": "UPDATE audit_events SET metadata_json='{}'::jsonb "
        "WHERE resource_id=:id AND action='INTAKE_ANSWER_PROPOSED'",
    }
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(statements[change]), {"id": status.operation_id})
        with pytest.raises(ApiError):
            _run(lambda sessions: read_answer(
                sessions, telegram_user_id=draft_owner[0], operation_id=status.operation_id,
            ))
    finally:
        engine.dispose()


def test_cancel_after_acceptance_is_not_a_rollback(draft_owner):
    status = proposed(draft_owner)
    _run(lambda sessions: accept_answer(
        sessions, telegram_user_id=draft_owner[0], operation_id=status.operation_id,
        request=consent(status),
    ))
    with pytest.raises(ApiError) as caught:
        _run(lambda sessions: cancel_answer(
            sessions, telegram_user_id=draft_owner[0], operation_id=status.operation_id,
        ))
    assert caught.value.code == "ANSWER_ALREADY_ACCEPTED"
