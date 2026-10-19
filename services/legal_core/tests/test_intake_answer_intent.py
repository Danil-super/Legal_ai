"""Distinct factual answers must not collide with each other or narrative understanding."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from legal_core.intake_answer_intent import AnswerPreparationIntent


def payload():
    return {
        "clinicId": str(uuid4()), "actorMembershipId": str(uuid4()),
        "draftId": str(uuid4()), "draftRevision": 2, "inputSha256": "a" * 64,
        "answerSha256": "b" * 64, "questionSha256": "c" * 64, "asOfDate": "2026-10-16",
    }


def test_answer_intent_is_stable_separate_and_contains_no_source_text():
    first = AnswerPreparationIntent.model_validate(payload())
    replay = AnswerPreparationIntent.model_validate(first.model_dump(mode="json", by_alias=True))
    assert first == replay
    assert first.idempotency_key == replay.idempotency_key
    assert first.request_sha256 == replay.request_sha256
    assert first.scope == f"draft-answer:v1:{first.draft_id}"
    assert first.operation_kind == "INTERPRET_ANSWER"
    assert not {"source", "answer", "value"} & set(first.model_dump(by_alias=True))


@pytest.mark.parametrize("field,value", [
    ("answerSha256", "d" * 64), ("questionSha256", "d" * 64),
    ("inputSha256", "d" * 64), ("draftRevision", 3), ("asOfDate", "2026-10-15"),
    ("clinicId", str(uuid4())), ("actorMembershipId", str(uuid4())),
])
def test_each_answer_source_and_server_question_binding_changes_identity(field, value):
    raw = payload()
    first = AnswerPreparationIntent.model_validate(raw)
    second = AnswerPreparationIntent.model_validate({**raw, field: value})
    assert first.request_sha256 != second.request_sha256
    assert first.idempotency_key != second.idempotency_key


@pytest.mark.parametrize("patch", [
    {"draftRevision": True}, {"draftRevision": "2"}, {"draftRevision": 0},
    {"answerSha256": "UPPER"}, {"questionSha256": ""}, {"asOfDate": "2026-02-30"},
    {"source": "must never be a client-controlled intent field"},
    {"operationKind": "UNDERSTAND_INTAKE"}, {"schemaVersion": "unknown"},
])
def test_answer_intent_refuses_non_strict_or_unrecognized_fields(patch):
    with pytest.raises(ValidationError):
        AnswerPreparationIntent.model_validate({**payload(), **patch})
