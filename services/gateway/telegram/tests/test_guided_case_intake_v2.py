# ruff: noqa: RUF001
"""Synthetic, deterministic checks for the owner-approved v2 intake language."""

from datetime import date

from legal_core.analysis_api import _analysis_date
from legal_core.api_contracts import FactInput
from legal_core.case_api import _input_value
from legal_core.intake import missing_facts_for
from telegram_gateway.case_wizard import facts_from_v2_data
from telegram_gateway.guided_case_intake_v2 import (
    V2_CONFIRM,
    V2_EVENT_DATE,
    V2_SERVICES,
    V2_SUMMARY,
    active_states,
    next_missing_state,
    parse_answer,
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
    assert next_missing_state(data) == V2_SUMMARY


def test_unknown_event_date_remains_a_targeted_follow_up() -> None:
    data = _base_data()
    data["eventDate"] = {"date": None, "precision": "UNKNOWN"}

    assert next_missing_state(data) == V2_EVENT_DATE


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
    assert next_missing_state(data) == V2_SUMMARY
