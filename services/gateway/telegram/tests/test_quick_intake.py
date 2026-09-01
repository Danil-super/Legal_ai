from datetime import date

import pytest

from telegram_gateway.quick_intake import (
    QuickIntakeError,
    QuickIntakePrivacyError,
    contains_probable_person_name,
    extract_quick_intake,
)

TODAY = date(2026, 9, 1)


def test_quick_intake_extracts_high_confidence_dental_conflict_candidates() -> None:
    result = extract_quick_intake(
        "Установили винир 2026-08-01. 2026-08-20 появился скол. "
        "2026-08-31 пациент потребовал вернуть 70 000 руб. "
        "Письменной претензии нет. О вреде здоровью не заявляет. "
        "Юрист не обращался. Угрожает обратиться в Роспотребнадзор. "
        "Все основные документы есть.",
        today=TODAY,
    )

    assert result.candidate_data["incident_type"] == "QUALITY_COMPLAINT"
    assert result.candidate_data["service_type"] == "установка винира"
    assert result.candidate_data["patient_demand"] == "REFUND_DEMAND"
    assert result.candidate_data["demand_amount_kopecks"] == 7_000_000
    assert result.candidate_data["formal_claim"] == "NO"
    assert result.candidate_data["harm_claimed"] == "NO"
    assert result.candidate_data["lawyer_contact"] == "NO"
    assert result.candidate_data["regulator_threat"] == "YES"
    assert "regulator_or_court" not in result.candidate_data
    assert result.candidate_data["documents_status"] == "COMPLETE"
    assert result.candidate_data["problem_summary"] == result.sanitized_text
    assert result.next_wizard_state in {
        "SERVICE_DATE",
        "INCIDENT_DATE",
        "CLAIM_DATE",
        "AUTHORITY",
    }
    assert set(result.draft_data) <= set(result.candidate_data)


def test_quick_intake_redacts_phone_email_and_long_identifier_locally() -> None:
    result = extract_quick_intake(
        "Скололся винир, пациент требует вернуть деньги. "
        "Контакт +7 999 123-45-67, почта patient@example.com, номер 1234567890123456.",
        today=TODAY,
    )

    assert "+7 999 123-45-67" not in result.sanitized_text
    assert "patient@example.com" not in result.sanitized_text
    assert "1234567890123456" not in result.sanitized_text
    assert "[PHONE]" in result.sanitized_text
    assert "[EMAIL]" in result.sanitized_text
    assert "[IDENTIFIER]" in result.sanitized_text
    assert result.redaction_counts["phone"] == 1
    assert result.redaction_counts["email"] == 1
    assert result.redaction_counts["long_identifier"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "Пациент Иванов Иван требует вернуть деньги за коронку.",
        "Пациент Иванов Иван Иванович требует вернуть деньги за коронку.",
        "Иванов И.И. требует вернуть деньги за коронку после лечения.",
        "И.И. Иванов требует вернуть деньги за коронку после лечения.",
    ],
)
def test_quick_intake_rejects_probable_person_names(text: str) -> None:
    assert contains_probable_person_name(text) is True
    with pytest.raises(QuickIntakePrivacyError):
        extract_quick_intake(text, today=TODAY)


def test_quick_intake_does_not_treat_absence_as_negative_fact() -> None:
    result = extract_quick_intake(
        "После установки коронки появилась трещина, пациент просит повторное лечение.",
        today=TODAY,
    )

    assert result.candidate_data["incident_type"] == "QUALITY_COMPLAINT"
    assert result.candidate_data["service_type"] == "установка коронки"
    assert result.candidate_data["patient_demand"] == "REWORK_DEMAND"
    for field in (
        "formal_claim",
        "harm_claimed",
        "lawyer_contact",
        "regulator_or_court",
        "regulator_threat",
        "documents_status",
    ):
        assert field not in result.candidate_data


def test_quick_intake_leaves_ambiguous_multiple_demands_for_wizard_confirmation() -> None:
    result = extract_quick_intake(
        "После имплантации пациент одновременно требует возврат денег и компенсацию вреда.",
        today=TODAY,
    )

    assert "patient_demand" not in result.candidate_data
    assert result.next_wizard_state in {"SERVICE_DATE", "PATIENT_DEMAND"}


@pytest.mark.parametrize(
    ("description", "expected_kopecks"),
    [
        ("Скололся винир, пациент требует вернуть 70 тыс. руб.", 7_000_000),
        ("Скололась коронка, пациент требует возврат 70000 рублей.", 7_000_000),
        ("Проблема с имплантом, пациент требует компенсацию 1,5 млн руб.", 150_000_000),
    ],
)
def test_quick_intake_parses_bounded_money_forms(
    description: str,
    expected_kopecks: int,
) -> None:
    result = extract_quick_intake(description, today=TODAY)

    assert result.candidate_data["demand_amount_kopecks"] == expected_kopecks


def test_dates_are_only_extracted_when_not_future_and_context_is_clear() -> None:
    result = extract_quick_intake(
        "Дата установки винира 2026-08-01. Дата проблемы 2026-08-20. "
        "Дата обращения пациента 2026-08-31. Требует возврат денег.",
        today=TODAY,
    )

    assert result.candidate_data["service_date"] == {
        "date": "2026-08-01",
        "precision": "EXACT",
    }
    assert result.candidate_data["incident_date"] == {
        "date": "2026-08-20",
        "precision": "EXACT",
    }
    assert result.candidate_data["claim_date"] == {
        "date": "2026-08-31",
        "precision": "EXACT",
    }

    future = extract_quick_intake(
        "Дата установки коронки 2026-09-10. Потом коронка сломалась, пациент требует возврат.",
        today=TODAY,
    )
    assert "service_date" not in future.candidate_data


def test_regulator_threat_and_actual_authority_are_not_conflated() -> None:
    threat = extract_quick_intake(
        "Пациент недоволен коронкой и угрожает обратиться в Роспотребнадзор.",
        today=TODAY,
    )
    assert threat.candidate_data["regulator_threat"] == "YES"
    assert "regulator_or_court" not in threat.candidate_data

    actual = extract_quick_intake(
        "Пациент недоволен коронкой. Клиника получила запрос Роспотребнадзора.",
        today=TODAY,
    )
    assert actual.candidate_data["regulator_or_court"] == "YES"
    assert "regulator_threat" not in actual.candidate_data


def test_quick_intake_persists_only_contiguous_prefix_before_first_missing_field() -> None:
    result = extract_quick_intake(
        "Скололся винир, пациент требует вернуть 70 тыс. руб. "
        "Письменной претензии нет. Юрист не обращался. "
        "Все основные документы есть.",
        today=TODAY,
    )

    assert result.next_wizard_state == "SERVICE_DATE"
    assert result.draft_data == {
        "incident_type": "QUALITY_COMPLAINT",
        "service_type": "установка винира",
    }
    assert "patient_demand" in result.candidate_data
    assert "patient_demand" in result.dropped_candidate_fields
    assert "documents_status" in result.dropped_candidate_fields


def test_quick_intake_rejects_short_or_oversized_description() -> None:
    with pytest.raises(QuickIntakeError):
        extract_quick_intake("коронка", today=TODAY)
    with pytest.raises(QuickIntakeError):
        extract_quick_intake("x" * 1501, today=TODAY)
