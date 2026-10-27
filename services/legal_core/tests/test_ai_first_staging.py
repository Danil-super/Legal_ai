"""Runtime-role multi-fact staging keeps consent, stale-input and tenant fences."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.case_api import ApiError
from legal_core.database import owner_database_url
from legal_core.intake_answer_contracts import AnswerInterpretation, derive_answer_question
from legal_core.intake_answer_operations import read_answer
from legal_core.intake_draft_staging import stage_answer
from legal_core.intake_factual_candidates import retained_answer_sources
from legal_core.intake_preparation_contracts import AcceptUnderstandingRequest
from test_ai_first_candidates import candidate
from test_case_api import actor_headers, application_client, seed_admin
from test_intake_answer_lifecycle import (
    complete,
    context,
    mutate,
    owner as lifecycle_owner,
    reserve,
    run,
    save_data,
)

owner = lifecycle_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
RICH = "Это обращение 15.10.2026. Пригласили на осмотр."


@pytest.fixture(autouse=True)
def enable(monkeypatch):
    monkeypatch.setenv("AI_FIRST_FACTUAL_INTAKE_ENABLED", "true")


def prepared(owner):
    lease = reserve(owner, answer=RICH)
    private = context(owner, lease)
    source = private.retained_sources[-1]
    event = next(item for item in private.allowed_questions if item.target_field == "eventDate")
    actions = next(item for item in private.allowed_questions
                   if item.target_field == "clinicActions")
    interpretation = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": "MESSAGE_OR_REQUEST",
        "sourceSpans": [{"start": 0, "end": len(RICH)}],
        "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
            candidate(event, source, {"date": "2026-10-15", "precision": "EXACT"}, "15.10.2026"),
            candidate(actions, source, ["INVITED_FOR_EXAMINATION"], "Пригласили на осмотр"),
        ]},
    })
    return lease, private, interpretation


def stage(owner, status):
    source = status.source
    return run(lambda sessions: stage_answer(
        sessions, telegram_user_id=owner[0], operation_id=status.operation_id,
        request=AcceptUnderstandingRequest(
            expectedRevision=source.draft_revision, expectedInputSha256=source.input_sha256,
            expectedProposalSha256=source.proposal_sha256,
        ),
    ))


def test_rich_answer_survives_restart_and_stages_all_facts_once_without_creating_case(owner):
    lease, private, value = prepared(owner)
    assert all(item.revision == owner[3]["revision"] for item in private.retained_sources)
    result = complete(owner, lease, value).response
    resumed = run(lambda sessions: read_answer(
        sessions, telegram_user_id=owner[0], operation_id=result.operation_id,
    )).response
    assert resumed == result
    saved = stage(owner, resumed)
    assert saved.draft.draft_data["eventDate"] == {"date": "2026-10-15", "precision": "EXACT"}
    assert saved.draft.draft_data["clinicActions"] == ["INVITED_FOR_EXAMINATION"]
    assert saved.staged_revision == owner[3]["revision"] + 1
    replay = stage(owner, resumed)
    assert replay == saved
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM cases WHERE clinic_id=:clinic"),
                                     {"clinic": owner[1]}) == 0
            assert connection.scalar(text(
                "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                "AND action='INTAKE_PROPOSAL_STAGED'"
            ), {"id": result.operation_id}) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("timing", ["before-complete", "before-stage"])
def test_editing_after_dispatch_never_replaces_newer_facts(owner, timing):
    lease, _, value = prepared(owner)
    status = None if timing == "before-complete" else complete(owner, lease, value).response
    edited = save_data(owner, {"eventDate": {"date": None, "precision": "UNKNOWN"}})
    with pytest.raises(ApiError) as caught:
        complete(owner, lease, value) if status is None else stage(owner, status)
    assert caught.value.status_code == 409
    with application_client() as client:
        current = client.get(f"/v1/telegram-intake-drafts/{edited[3]['id']}",
                             headers=actor_headers(owner[0])).json()
    assert current["draftData"]["eventDate"]["precision"] == "UNKNOWN"
    assert "clinicActions" not in current["draftData"]


def test_proposal_cannot_change_sources_after_completion(owner):
    lease, _, value = prepared(owner)
    status = complete(owner, lease, value).response
    mutate("UPDATE idempotency_records SET response_json = jsonb_set(response_json, "
           "'{retainedSources,1,text}', to_jsonb(CAST(:text AS text))) WHERE id=:id",
           {"id": status.operation_id, "text": "Изменённый синтетический ответ."})
    with pytest.raises(ApiError):
        stage(owner, status)


@pytest.mark.parametrize("access", ["other-clinic", "revoked"])
def test_other_or_revoked_actor_cannot_stage_a_multi_fact_proposal(owner, access):
    lease, _, value = prepared(owner)
    status = complete(owner, lease, value).response
    caller = owner[0]
    if access == "revoked":
        mutate("UPDATE clinic_users SET status='REVOKED' WHERE id=:id", {"id": owner[2]})
    else:
        caller = 38_000_000_000 + uuid4().int % 100_000_000
        seed_admin(caller)
    with application_client() as client:
        result = client.post(f"/v1/intake-answer-preparations/{status.operation_id}/draft-staging",
                             headers=actor_headers(caller), json={
                                 "expectedRevision": status.source.draft_revision,
                                 "expectedInputSha256": status.source.input_sha256,
                                 "expectedProposalSha256": status.source.proposal_sha256,
                             })
    assert result.status_code in {403, 404}


def test_disabled_feature_keeps_legacy_context_and_refuses_new_producer(owner, monkeypatch):
    monkeypatch.setenv("AI_FIRST_FACTUAL_INTAKE_ENABLED", "false")
    lease = reserve(owner, answer=RICH)
    private = context(owner, lease)
    assert private.retained_sources is None and private.allowed_questions is None
    assert "retainedSources" not in private.model_dump(mode="json", by_alias=True)
    sources = retained_answer_sources(lease.operation_id, owner[3]["revision"],
                                      owner[3]["draftData"], RICH)
    event = derive_answer_question(owner[3]["draftData"], "EVENT_DATE")
    forged = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": "MESSAGE_OR_REQUEST",
        "sourceSpans": [{"start": 0, "end": len(RICH)}],
        "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
            candidate(event, sources[-1], {"date": "2026-10-15", "precision": "EXACT"},
                      "15.10.2026"),
        ]},
    })
    with pytest.raises(ApiError) as caught:
        complete(owner, lease, forged)
    assert caught.value.status_code == 422
