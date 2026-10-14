# ruff: noqa: RUF001
"""Synthetic, deterministic checks for the owner-approved v2 intake language."""

from datetime import date

import pytest
from legal_core.analysis_api import _analysis_date
from legal_core.analysis_clarifications import ClarificationAnswer, ClarificationRequest
from legal_core.api_contracts import FactInput
from legal_core.case_api import _input_value
from legal_core.intake import missing_facts_for
from telegram_gateway.case_wizard import facts_from_v2_data
from telegram_gateway.guided_case_intake_v2 import (
    V2_CONFIRM,
    V2_SAFETY,
    V2_SERVICES,
    active_states,
    apply_clarification_answer,
    clarification_choices,
    clarification_question,
    invalidate_service_clarification_answers,
    next_missing_state,
    parse_answer,
    parse_clarification_answer,
    review_blocks,
)


def _base_data() -> dict[str, object]:
    return {
        "intakeVersion": 2,
        "incomingKind": "COMPLAINT",
        "incomingSourceStatus": "NOT_ATTACHED",
        "situationAreas": ["TREATMENT", "SERVICE"],
        "affectedServices": ["терапевтическое лечение"],
        "eventSummary": "После лечения клиент сообщил о дискомфорте.",
        "eventDate": {"date": "2026-09-01", "precision": "EXACT"},
        "conflictStage": "ONGOING",
        "clinicActions": ["INVITED_FOR_EXAMINATION"],
        "healthSignals": ["NO_KNOWN_INFORMATION"],
        "caseMaterialsStatus": "NOT_ATTACHED",
    }


def test_treatment_requires_service_but_other_topics_do_not() -> None:
    data = _base_data()
    data.pop("affectedServices")

    assert next_missing_state(data) == V2_SERVICES

    data["situationAreas"] = ["PERSONAL_DATA"]
    assert next_missing_state(data) == V2_SAFETY


def test_unknown_event_date_does_not_hide_later_urgent_facts() -> None:
    data = _base_data()
    data["eventDate"] = {"date": None, "precision": "UNKNOWN"}

    assert next_missing_state(data) == V2_SAFETY


def test_v2_wizard_facts_reach_legal_core_analysis_date_selection() -> None:
    mapped = [FactInput.model_validate(item) for item in facts_from_v2_data(_base_data())]
    domain_facts = {
        item.fact_key: _input_value(item.value_type, item.value)
        for item in mapped
    }

    assert missing_facts_for(domain_facts) == []
    assert _analysis_date(domain_facts) == date(2026, 9, 1)


def test_summary_uses_plain_language_and_preserves_unknown_not_negative() -> None:
    data = _base_data()
    data["healthSignals"] = ["UNKNOWN"]

    blocks = review_blocks(data, final=True)
    rendered = "\n".join(blocks)

    assert "Краткая фабула" in rendered
    assert "недостаточно" in rendered.casefold()
    assert "сведений об ухудшении здоровья нет" not in rendered.casefold()
    assert "претензи" not in rendered.casefold()
    assert "юридическ" not in rendered.casefold()


def test_v2_actions_use_closed_tokens_and_anonymised_text_validation() -> None:
    assert parse_answer("incomingKind", "COMPLAINT") == "COMPLAINT"
    assert parse_answer("clinicActions", "INVITED_FOR_EXAMINATION,OTHER") == [
        "INVITED_FOR_EXAMINATION",
        "OTHER",
    ]
    assert parse_answer("eventDate", "неизвестно") == {
        "date": None,
        "precision": "UNKNOWN",
    }
    assert parse_answer("eventDate", "01.09.2026") == {
        "date": "2026-09-01",
        "precision": "EXACT",
    }

    try:
        parse_answer("eventSummary", "Пациент Иван Иванов сообщил о проблеме после лечения.")
    except ValueError as exc:
        assert "ФИО" in str(exc)
    else:  # pragma: no cover - assertion makes the privacy contract explicit
        raise AssertionError("personal name must not be accepted")


def test_v2_sequence_ends_in_deterministic_summary() -> None:
    data = _base_data()

    assert active_states(data)[-1] == V2_CONFIRM
    assert next_missing_state(data) == V2_SAFETY


def _request(question: str, slot: int | None = None) -> ClarificationRequest:
    return ClarificationRequest.model_validate({"questionId": question, "serviceSlot": slot})


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("SERVICE_PAYMENT_BASIS", "Как оплачивалась услуга «протезирование»?"),
        ("SERVICE_PROVIDED_DATE", "Когда была оказана услуга «протезирование»?"),
    ],
)
def test_clarification_questions_identify_only_the_requested_service(
    question: str, expected: str,
) -> None:
    data = _base_data() | {"affectedServices": ["лечение", "протезирование"]}
    rendered = clarification_question(_request(question, 1), data)
    assert rendered.startswith(expected)
    assert "лечение»" not in rendered
    assert "претензи" not in rendered.casefold()
    assert "ответственност" not in rendered.casefold()


def test_service_date_question_distinguishes_it_from_event_date_and_explains_precision() -> None:
    rendered = clarification_question(_request("SERVICE_PROVIDED_DATE", 0), _base_data())
    assert "дату события" in rendered
    assert "примерно ГГГГ-ММ-ДД" in rendered
    assert "неизвестно" in rendered


def test_response_purpose_question_is_global_without_services() -> None:
    assert clarification_question(_request("RESPONSE_PURPOSE"), {}) == (
        "Что клинике нужно сделать в связи с обращением?"
    )


@pytest.mark.parametrize("services", [None, [], ["лечение"] * 6, ["лечение", 7]])
def test_service_question_rejects_invalid_existing_service_list(services: object) -> None:
    with pytest.raises(ValueError):
        clarification_question(_request("SERVICE_PAYMENT_BASIS", 0), {"affectedServices": services})


def test_service_question_rejects_slot_missing_from_existing_services() -> None:
    with pytest.raises(ValueError):
        clarification_question(_request("SERVICE_PAYMENT_BASIS", 1), _base_data())


def test_question_rejects_injected_wording_even_with_allowlisted_id() -> None:
    with pytest.raises(ValueError):
        clarification_question(
            {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "text": "произвольный текст"},
            _base_data(),
        )


@pytest.mark.parametrize(
    ("question", "slot", "values"),
    [
        ("SERVICE_PAYMENT_BASIS", 0, {"PAID", "PUBLICLY_COVERED", "OTHER", "UNKNOWN"}),
        ("RESPONSE_PURPOSE", None, {"RECEIVE_AND_EXPLAIN", "ANSWER_REQUEST", "OTHER", "UNKNOWN"}),
        ("SERVICE_PROVIDED_DATE", 0, {"UNKNOWN"}),
    ],
)
def test_clarification_choices_are_bounded_and_include_unknown(
    question: str, slot: int | None, values: set[str],
) -> None:
    choices = clarification_choices(_request(question, slot))
    assert {value for value, _ in choices} == values
    assert all(label and label != value for value, label in choices)


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("2020-09-01", {"date": "2020-09-01", "precision": "EXACT"}),
        ("01.09.2020", {"date": "2020-09-01", "precision": "EXACT"}),
        ("примерно 2020-09-01", {"date": "2020-09-01", "precision": "APPROXIMATE"}),
        ("примерно 01.09.2020", {"date": "2020-09-01", "precision": "APPROXIMATE"}),
        ("не знаю", {"date": None, "precision": "UNKNOWN"}),
        ("UNKNOWN", {"date": None, "precision": "UNKNOWN"}),
    ],
)
def test_clarification_date_parses_explicit_user_precision(raw: str, value: object) -> None:
    answer = parse_clarification_answer(_request("SERVICE_PROVIDED_DATE", 0), raw)
    assert isinstance(answer, ClarificationAnswer)
    assert answer.model_dump(mode="json", by_alias=True) == {
        "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0, "value": value,
    }


@pytest.mark.parametrize("raw", ["2020-02-30", "примерно неизвестно", "2099-01-01", "завтра"])
def test_clarification_date_rejects_unbounded_or_inconsistent_answers(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_clarification_answer(_request("SERVICE_PROVIDED_DATE", 0), raw)


def test_clarification_payment_accepts_only_closed_user_choice() -> None:
    answer = parse_clarification_answer(_request("SERVICE_PAYMENT_BASIS", 0), " paid ")
    assert answer.value == "PAID"
    with pytest.raises(ValueError):
        parse_clarification_answer(_request("SERVICE_PAYMENT_BASIS", 0), "пациент оплатил картой")


def test_apply_clarification_answer_preserves_other_answers_and_updates_unique_identity() -> None:
    data = _base_data() | {"affectedServices": ["лечение", "протезирование"]}
    original = dict(data)
    answered = apply_clarification_answer(data, _request("SERVICE_PAYMENT_BASIS", 0), "PAID")
    answered = apply_clarification_answer(answered, _request("SERVICE_PAYMENT_BASIS", 1), "UNKNOWN")
    answered = apply_clarification_answer(answered, _request("RESPONSE_PURPOSE"), "ANSWER_REQUEST")
    updated = apply_clarification_answer(answered, _request("SERVICE_PAYMENT_BASIS", 0), "OTHER")
    assert data == original
    assert answered["analysisClarificationAnswers"]["answers"][0]["value"] == "PAID"
    assert updated["analysisClarificationAnswers"] == {"answers": [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "OTHER"},
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 1, "value": "UNKNOWN"},
        {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "ANSWER_REQUEST"},
    ]}


def test_all_eleven_distinct_answers_are_bounded_without_changing_event_date() -> None:
    data = _base_data() | {"affectedServices": [f"услуга {index}" for index in range(5)]}
    for slot in range(5):
        data = apply_clarification_answer(data, _request("SERVICE_PAYMENT_BASIS", slot), "UNKNOWN")
        data = apply_clarification_answer(
            data, _request("SERVICE_PROVIDED_DATE", slot), "2020-01-01",
        )
    data = apply_clarification_answer(data, _request("RESPONSE_PURPOSE"), "UNKNOWN")
    assert len(data["analysisClarificationAnswers"]["answers"]) == 11
    assert data["eventDate"] == _base_data()["eventDate"]


def test_apply_rejects_service_scope_outside_existing_draft() -> None:
    with pytest.raises(ValueError):
        apply_clarification_answer(_base_data(), _request("SERVICE_PAYMENT_BASIS", 1), "PAID")


def test_apply_rejects_existing_duplicate_answers_instead_of_silently_repairing() -> None:
    answer = {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "PAID"}
    data = _base_data() | {"analysisClarificationAnswers": {"answers": [answer, answer]}}
    with pytest.raises(ValueError):
        apply_clarification_answer(data, _request("RESPONSE_PURPOSE"), "UNKNOWN")


@pytest.mark.parametrize("services", [["протезирование", "лечение"], ["осмотр"], None])
def test_changed_services_discard_scoped_answers_and_preserve_global_purpose(
    services: list[str] | None,
) -> None:
    data = _base_data() | {"affectedServices": ["лечение", "протезирование"]}
    data = apply_clarification_answer(data, _request("SERVICE_PAYMENT_BASIS", 1), "PAID")
    data = apply_clarification_answer(data, _request("RESPONSE_PURPOSE"), "OTHER")
    updated = invalidate_service_clarification_answers(data, services)
    assert updated["analysisClarificationAnswers"] == {"answers": [
        {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "OTHER"},
    ]}
    assert len(data["analysisClarificationAnswers"]["answers"]) == 2
    assert updated.get("affectedServices") == services


def test_unchanged_services_retain_their_answers() -> None:
    data = apply_clarification_answer(_base_data(), _request("SERVICE_PAYMENT_BASIS", 0), "PAID")
    assert invalidate_service_clarification_answers(data, ["терапевтическое лечение"]) == data


def test_recap_names_each_service_and_preserves_unknown_and_approximate_dates() -> None:
    data = _base_data() | {"affectedServices": ["лечение", "протезирование"]}
    data = apply_clarification_answer(data, _request("SERVICE_PAYMENT_BASIS", 0), "PAID")
    data = apply_clarification_answer(data, _request("SERVICE_PAYMENT_BASIS", 1), "UNKNOWN")
    data = apply_clarification_answer(
        data, _request("SERVICE_PROVIDED_DATE", 1), "примерно 2020-01-01",
    )
    data = apply_clarification_answer(data, _request("RESPONSE_PURPOSE"), "UNKNOWN")
    rendered = "\n".join(review_blocks(data))
    assert "Оплата услуги «лечение»: Платно" in rendered
    assert "Оплата услуги «протезирование»: Неизвестно" in rendered
    assert "Дата услуги «протезирование»: примерно 2020-01-01" in rendered
    assert "Цель ответа: Неизвестно" in rendered
    assert "Когда: 2026-09-01" in rendered


def test_recap_does_not_present_model_requests_as_confirmed_answers() -> None:
    data = _base_data() | {"analysisClarificationRequests": [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
    ]}
    assert "Оплата услуги" not in "\n".join(review_blocks(data))
