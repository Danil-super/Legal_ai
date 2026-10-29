"""Real Core completion, ownership, provisional writes and replay for partial answers."""

import os
from copy import deepcopy
from uuid import uuid4

import pytest

from legal_core.case_api import ApiError
from legal_core.intake_answer_contracts import AnswerInterpretation
from legal_core.intake_answer_lifecycle import complete_answer, read_answer_context
from legal_core.intake_answer_operations import reserve_answer
from test_case_api import actor_headers, application_client
from test_intake_answer_consent import consent
from test_intake_draft_staging import endpoint, sql, stage
from test_intake_preparation_lifecycle import _run
from test_intake_preparation_lifecycle import draft_owner as draft_owner
from test_secondary_factual_staging import ANSWER, DATE, PURPOSE_UNKNOWN, partial_bundle

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


def partial_operation(draft_owner, monkeypatch):
    monkeypatch.setenv("AI_FIRST_FACTUAL_INTAKE_ENABLED", "true")
    actor, _clinic, _membership, old = draft_owner
    data, *_ = partial_bundle()
    with application_client() as client:
        saved = client.put(f"/v1/telegram-intake-drafts/{old['id']}",
                           headers=actor_headers(actor, uuid4()), json={
                               "expectedRevision": old["revision"],
                               "wizardState": "ANALYSIS_CLARIFICATION", "draftData": data,
                           })
        assert saved.status_code == 200, saved.text
        draft = saved.json()
    claim = _run(lambda sessions: reserve_answer(
        sessions, telegram_user_id=actor, draft_id=old["id"],
        expected_revision=draft["revision"], answer=ANSWER,
    ))
    context = _run(lambda sessions: read_answer_context(
        sessions, telegram_user_id=actor, operation_id=claim.response.operation_id,
        lease_token=claim.lease_token, expected_revision=draft["revision"],
        expected_input_sha256=claim.intent.input_sha256,
    ))
    *_, raw = partial_bundle(operation=claim.response.operation_id,
                             revision=draft["revision"], data=data)
    return actor, draft, claim, context, raw


def complete(actor, claim, raw):
    return _run(lambda sessions: complete_answer(
        sessions, telegram_user_id=actor, operation_id=claim.response.operation_id,
        lease_token=claim.lease_token, interpretation=AnswerInterpretation.model_validate(raw),
    )).response


def test_partial_completion_and_idempotent_staging_keep_primary_unanswered(
    draft_owner, monkeypatch,
):
    actor, draft, claim, context, raw = partial_operation(draft_owner, monkeypatch)
    before = deepcopy(draft)
    assert len(context.retained_sources) == 2
    status = complete(actor, claim, raw)
    assert status.state == "UNRESOLVED" and status.interpretation.value is None
    request = consent(status)
    with application_client() as client:
        rejected = client.post(endpoint("answer", status.operation_id) + "/acceptance",
                               headers=actor_headers(actor), json=request.model_dump(by_alias=True))
        assert rejected.status_code == 409
        assert client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                          headers=actor_headers(actor)).json() == before
        staged = stage(client, actor, status, request, "answer")
        assert staged.status_code == 200, staged.text
        result = staged.json()
        assert result["draft"]["draftData"]["analysisClarificationAnswers"]["answers"] == [
            PURPOSE_UNKNOWN,
            {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0, "value": DATE},
        ]
        assert result["draft"]["wizardState"] == "ANALYSIS_CLARIFICATION"
        assert result["stagedRevision"] == before["revision"] + 1
        assert stage(client, actor, status, request, "answer").json() == result
    assert sql("SELECT count(*) FROM audit_events WHERE resource_id=:id "
               "AND action='INTAKE_PROPOSAL_STAGED'", {"id": status.operation_id}) == 1
    assert sql("SELECT count(*) FROM audit_events WHERE resource_id=:id "
               "AND action='INTAKE_ANSWER_ACCEPTED'", {"id": status.operation_id}) == 0
    assert sql("SELECT count(*) FROM cases WHERE clinic_id=:id", {"id": draft_owner[1]}) == 0


@pytest.mark.parametrize("invalid", ["primary-target", "foreign-service", "foreign-source"])
def test_core_independently_rejects_forged_secondary_only_proposal(
    draft_owner, monkeypatch, invalid,
):
    actor, _draft, claim, context, raw = partial_operation(draft_owner, monkeypatch)
    addition = raw["factualProposal"]["candidates"][0]
    if invalid == "primary-target":
        addition.update(question=context.question.model_dump(mode="json", by_alias=True),
                        value="PAID")
    elif invalid == "foreign-service":
        addition["question"]["clarificationRequest"]["serviceSlot"] = 1
    else:
        addition["anchors"][0]["sourceId"] = str(uuid4())
    with pytest.raises(ApiError) as error:
        complete(actor, claim, raw)
    assert error.value.code == "ANSWER_PROPOSAL_INVALID"


@pytest.mark.parametrize("invalid", ["stale-revision", "lost-lease", "foreign-actor"])
def test_partial_completion_keeps_current_revision_lease_and_actor_fences(
    draft_owner, monkeypatch, invalid,
):
    actor, draft, claim, _context, raw = partial_operation(draft_owner, monkeypatch)
    if invalid == "stale-revision":
        sql("UPDATE telegram_intake_drafts SET revision=revision+1 WHERE id=:id",
            {"id": draft["id"]})
    elif invalid == "lost-lease":
        sql("UPDATE idempotency_records SET lease_token=:token WHERE id=:id",
            {"id": claim.response.operation_id, "token": uuid4()})
    else:
        actor += 1_000_000_000
    with pytest.raises(ApiError):
        complete(actor, claim, raw)
    assert sql("SELECT count(*) FROM audit_events WHERE resource_id=:id "
               "AND action='INTAKE_PROPOSAL_STAGED'", {"id": claim.response.operation_id}) == 0


def test_unresolved_without_secondary_facts_cannot_stage_noop(draft_owner, monkeypatch):
    actor, draft, claim, _context, raw = partial_operation(draft_owner, monkeypatch)
    raw.pop("factualProposal")
    status = complete(actor, claim, raw)
    with application_client() as client:
        rejected = stage(client, actor, status, consent(status), "answer")
        assert rejected.status_code == 409
        assert client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                          headers=actor_headers(actor)).json() == draft
