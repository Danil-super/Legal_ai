"""Independent FC1 wire-contract examples from the approved clarification specification."""

import json
from datetime import date

import pytest
from pydantic import ValidationError

from legal_core.analysis_clarifications import (
    AnalysisClarificationAnswers,
    ClarificationRequest,
    ClarificationRequestBatch,
)


PAYMENT = "SERVICE_PAYMENT_BASIS"
SERVICE_DATE = "SERVICE_PROVIDED_DATE"
PURPOSE = "RESPONSE_PURPOSE"


@pytest.mark.parametrize("question_id", [PAYMENT, SERVICE_DATE])
@pytest.mark.parametrize("slot", [0, 1, 2, 3, 4])
def test_service_request_round_trips_every_existing_service_slot(
    question_id: str, slot: int
) -> None:
    payload = {"questionId": question_id, "serviceSlot": slot}
    request = ClarificationRequest.model_validate(payload)

    assert request.model_dump(mode="json", by_alias=True, exclude_none=True) == payload
    assert (
        ClarificationRequest.model_validate_json(request.model_dump_json(by_alias=True)) == request
    )


@pytest.mark.parametrize(
    "payload", [{"questionId": PURPOSE}, {"questionId": PURPOSE, "serviceSlot": None}]
)
def test_purpose_request_has_no_service_binding(payload: dict[str, object]) -> None:
    request = ClarificationRequest.model_validate(payload)

    assert request.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "questionId": PURPOSE
    }


@pytest.mark.parametrize("question_id", [PAYMENT, SERVICE_DATE])
@pytest.mark.parametrize("slot", [-1, 5, True, False, "0", 0.0, None, [], {}])
def test_service_request_rejects_out_of_bounds_or_coerced_slots(
    question_id: str, slot: object
) -> None:
    with pytest.raises(ValidationError):
        ClarificationRequest.model_validate({"questionId": question_id, "serviceSlot": slot})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"questionId": PAYMENT},
        {"questionId": SERVICE_DATE},
        {"questionId": "STATUTORY_DEADLINE", "serviceSlot": 0},
        {"questionId": "service_payment_basis", "serviceSlot": 0},
        {"questionId": True, "serviceSlot": 0},
        {"questionId": None, "serviceSlot": 0},
        {"questionId": PURPOSE, "serviceSlot": 0},
    ],
)
def test_request_rejects_unknown_question_or_invalid_service_binding(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ClarificationRequest.model_validate(payload)


@pytest.mark.parametrize("field", ["questionText", "value", "clinicId", "patientName", "url"])
def test_request_cannot_carry_arbitrary_text_answers_or_caller_scope(field: str) -> None:
    with pytest.raises(ValidationError):
        ClarificationRequest.model_validate({"questionId": PURPOSE, field: "synthetic-only"})


def test_request_batch_accepts_empty_and_three_distinct_requests() -> None:
    assert ClarificationRequestBatch.model_validate({"requests": []}).requests == []
    payload = {
        "requests": [
            {"questionId": PAYMENT, "serviceSlot": 0},
            {"questionId": SERVICE_DATE, "serviceSlot": 0},
            {"questionId": PAYMENT, "serviceSlot": 1},
        ]
    }

    batch = ClarificationRequestBatch.model_validate(payload)

    assert batch.model_dump(mode="json", by_alias=True, exclude_none=True) == payload


@pytest.mark.parametrize(
    "requests",
    [
        [{"questionId": PAYMENT, "serviceSlot": 0}] * 2,
        [{"questionId": PURPOSE}, {"questionId": PURPOSE, "serviceSlot": None}],
        [{"questionId": PAYMENT, "serviceSlot": slot} for slot in range(4)],
    ],
)
def test_request_batch_rejects_duplicate_identity_or_more_than_three(
    requests: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        ClarificationRequestBatch.model_validate({"requests": requests})


@pytest.mark.parametrize(
    "question_id,slot,value",
    [
        *[(PAYMENT, 0, value) for value in ("PAID", "PUBLICLY_COVERED", "OTHER", "UNKNOWN")],
        *[
            (PURPOSE, None, value)
            for value in ("RECEIVE_AND_EXPLAIN", "ANSWER_REQUEST", "OTHER", "UNKNOWN")
        ],
        (SERVICE_DATE, 4, {"date": "2024-02-29", "precision": "EXACT"}),
        (SERVICE_DATE, 0, {"date": "2026-01-01", "precision": "APPROXIMATE"}),
        (SERVICE_DATE, 1, {"date": None, "precision": "UNKNOWN"}),
    ],
)
def test_allowlisted_answers_round_trip(question_id: str, slot: int | None, value: object) -> None:
    answer = {"questionId": question_id, "value": value}
    if slot is not None:
        answer["serviceSlot"] = slot
    payload = {"answers": [answer]}

    answers = AnalysisClarificationAnswers.model_validate_json(json.dumps(payload))

    serialized = answers.model_dump(mode="json", by_alias=True)["answers"][0]
    assert serialized["questionId"] == question_id
    assert serialized.get("serviceSlot") == slot
    assert serialized["value"] == value
    assert (
        AnalysisClarificationAnswers.model_validate_json(answers.model_dump_json(by_alias=True))
        == answers
    )


@pytest.mark.parametrize(
    "question_id,slot,value",
    [
        (PAYMENT, 0, "ANSWER_REQUEST"),
        (PAYMENT, 0, "paid"),
        (PAYMENT, 0, "synthetic payer and bank account"),
        (PAYMENT, 0, True),
        (PAYMENT, 0, 1),
        (PAYMENT, 0, None),
        (PAYMENT, 0, {"value": "PAID"}),
        (PURPOSE, None, "PAID"),
        (PURPOSE, None, "answer_request"),
        (PURPOSE, None, False),
        (PURPOSE, None, None),
        (SERVICE_DATE, 0, "2026-01-01"),
        (SERVICE_DATE, 0, None),
        (SERVICE_DATE, 0, {}),
        (SERVICE_DATE, 0, {"date": "2026-01-01", "precision": "DAY"}),
        (SERVICE_DATE, 0, {"date": None, "precision": "EXACT"}),
        (SERVICE_DATE, 0, {"date": None, "precision": "APPROXIMATE"}),
        (SERVICE_DATE, 0, {"date": "2026-01-01", "precision": "UNKNOWN"}),
    ],
)
def test_answer_values_must_match_the_question_and_date_precision(
    question_id: str, slot: int | None, value: object
) -> None:
    answer = {"questionId": question_id, "value": value}
    if slot is not None:
        answer["serviceSlot"] = slot

    with pytest.raises(ValidationError):
        AnalysisClarificationAnswers.model_validate({"answers": [answer]})


@pytest.mark.parametrize(
    "invalid_date",
    [
        "2025-02-29",
        "2026-02-30",
        "2026-13-01",
        "2026-00-01",
        "0000-01-01",
        "2026-1-01",
        "20260101",
        "01.01.2026",
        "2026-01-01T00:00:00Z",
        " 2026-01-01",
        "2026-01-01 ",
        "synthetic date",
        True,
        20260101,
        date(2026, 1, 1),
    ],
)
def test_service_date_accepts_only_canonical_valid_calendar_strings(invalid_date: object) -> None:
    with pytest.raises(ValidationError):
        AnalysisClarificationAnswers.model_validate(
            {
                "answers": [
                    {
                        "questionId": SERVICE_DATE,
                        "serviceSlot": 0,
                        "value": {"date": invalid_date, "precision": "EXACT"},
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    "answer",
    [
        {"questionId": PAYMENT, "serviceSlot": 0},
        {"questionId": PAYMENT, "value": "PAID"},
        {"questionId": PAYMENT, "serviceSlot": True, "value": "PAID"},
        {"questionId": PAYMENT, "serviceSlot": "0", "value": "PAID"},
        {"questionId": PAYMENT, "serviceSlot": 5, "value": "PAID"},
        {"questionId": PURPOSE, "serviceSlot": 0, "value": "UNKNOWN"},
        {"questionId": "LEGAL_QUALIFICATION", "value": "UNKNOWN"},
    ],
)
def test_answer_requires_the_same_bounded_identity_as_a_request(answer: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AnalysisClarificationAnswers.model_validate({"answers": [answer]})


@pytest.mark.parametrize(
    "payload",
    [
        {"answers": [], "clinicId": "synthetic-clinic"},
        {"requests": [], "questionText": "synthetic question"},
    ],
)
def test_batch_envelopes_forbid_extra_fields(payload: dict[str, object]) -> None:
    model = AnalysisClarificationAnswers if "answers" in payload else ClarificationRequestBatch
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "answer",
    [
        {"questionId": PAYMENT, "serviceSlot": 0, "value": "PAID", "clinicId": "synthetic-clinic"},
        {"questionId": PURPOSE, "value": "UNKNOWN", "patientName": "synthetic-only"},
        {
            "questionId": SERVICE_DATE,
            "serviceSlot": 0,
            "value": {
                "date": None,
                "precision": "UNKNOWN",
                "medicalDetails": "synthetic-only",
            },
        },
    ],
)
def test_answer_and_nested_date_forbid_extra_scope_or_personal_data(
    answer: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AnalysisClarificationAnswers.model_validate({"answers": [answer]})


def test_answer_batch_accepts_empty_and_all_eleven_distinct_identities() -> None:
    assert AnalysisClarificationAnswers.model_validate({"answers": []}).answers == []
    items = (
        [{"questionId": PAYMENT, "serviceSlot": slot, "value": "UNKNOWN"} for slot in range(5)]
        + [
            {
                "questionId": SERVICE_DATE,
                "serviceSlot": slot,
                "value": {"date": None, "precision": "UNKNOWN"},
            }
            for slot in range(5)
        ]
        + [{"questionId": PURPOSE, "value": "UNKNOWN"}]
    )

    answers = AnalysisClarificationAnswers.model_validate({"answers": items})

    assert len(answers.answers) == 11
    assert json.loads(answers.model_dump_json(by_alias=True))["answers"][5]["value"] == {
        "date": None,
        "precision": "UNKNOWN",
    }


@pytest.mark.parametrize(
    "items",
    [
        [
            {"questionId": PAYMENT, "serviceSlot": 0, "value": value}
            for value in ("PAID", "UNKNOWN")
        ],
        [
            {
                "questionId": SERVICE_DATE,
                "serviceSlot": 0,
                "value": {
                    "date": value,
                    "precision": "EXACT",
                },
            }
            for value in ("2026-01-01", "2026-01-02")
        ],
        [
            {"questionId": PURPOSE, "value": "UNKNOWN"},
            {"questionId": PURPOSE, "serviceSlot": None, "value": "OTHER"},
        ],
    ],
)
def test_answer_identity_is_unique_even_when_values_differ(items: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError):
        AnalysisClarificationAnswers.model_validate({"answers": items})


def test_answer_batch_enforces_eleven_item_limit() -> None:
    with pytest.raises(ValidationError) as exc_info:
        AnalysisClarificationAnswers.model_validate(
            {"answers": [{"questionId": PAYMENT, "serviceSlot": 0, "value": "UNKNOWN"}] * 12}
        )

    assert any(error["type"] == "too_long" for error in exc_info.value.errors())
