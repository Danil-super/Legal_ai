"""Wire boundaries cannot promote an unresolved answer or accept client routing."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from legal_core.intake_answer_contracts import AnswerQuestion
from legal_core.intake_answer_wire import AnswerStatusResponse, StartAnswerRequest


def question():
    return AnswerQuestion(schemaVersion="intake-answer-question.v1", wizardState="INCOMING",
                          targetField="incomingKind")


def source():
    return {"draftId": str(uuid4()), "draftRevision": 2,
            "inputSha256": "a" * 64, "proposalSha256": "b" * 64}


@pytest.mark.parametrize("state", ["PENDING", "MANUAL_CONTINUATION"])
def test_pending_or_manual_receipt_does_not_expose_value_or_private_input(state):
    result = AnswerStatusResponse(operationId=uuid4(), state=state)
    assert result.model_dump(mode="json", by_alias=True) == {
        "operationId": str(result.operation_id), "state": state, "question": None,
        "interpretation": None, "source": None, "acceptance": None,
    }


@pytest.mark.parametrize("state", ["PROPOSED", "UNRESOLVED"])
def test_value_and_abstention_are_explicitly_distinct(state):
    interpretation = ({"status": "PROPOSED", "value": "COMPLAINT",
                       "sourceSpans": [{"start": 0, "end": 3}]} if state == "PROPOSED" else {
                           "status": "UNRESOLVED", "value": None, "sourceSpans": [],
                           "reasonCode": "AMBIGUOUS",
                       })
    result = AnswerStatusResponse(operationId=uuid4(), state=state, question=question(),
                                  source=source(), interpretation=interpretation)
    assert result.interpretation.status == state
    assert result.acceptance is None


@pytest.mark.parametrize("patch", [
    {"state": "PROPOSED"}, {"state": "UNRESOLVED"}, {"state": "ACCEPTED"},
    {"state": "PENDING", "question": question()},
    {"state": "PENDING", "source": source()}, {"leaseToken": str(uuid4())},
    {"answer": "unconfirmed input cannot be in the public receipt"},
    {"state": "ACCEPTED", "acceptance": {"draftId": str(uuid4()), "acceptedRevision": True}},
])
def test_incoherent_status_or_private_payload_is_rejected(patch):
    with pytest.raises(ValidationError):
        AnswerStatusResponse.model_validate({"operationId": str(uuid4()),
                                             "state": "PENDING", **patch})


@pytest.mark.parametrize("patch", [
    {"expectedRevision": True}, {"expectedRevision": "2"}, {"answer": ""},
    {"answer": "x" * 1501}, {"targetField": "incomingKind"}, {"question": question()},
    {"clinicId": str(uuid4())}, {"confirmed": True},
])
def test_start_refuses_client_question_authority_or_invalid_source(patch):
    with pytest.raises(ValidationError):
        StartAnswerRequest.model_validate({"expectedRevision": 2, "answer": "нет", **patch})


def test_short_explicit_answer_is_valid_not_an_empty_initial_narrative():
    assert StartAnswerRequest(expectedRevision=2, answer="нет").answer == "нет"
