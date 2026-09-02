# ruff: noqa: RUF001

from datetime import date

from telegram_gateway.quick_intake import extract_quick_intake

TODAY = date(2026, 9, 2)


def test_quick_intake_redacts_spaced_initials_before_persistable_summary() -> None:
    result = extract_quick_intake(
        "После установки коронки пациент Иванов И. И. сообщил о сколе и требует переделать работу.",
        today=TODAY,
    )

    assert "Иванов И. И." not in result.sanitized_text
    assert "[PERSON_NAME]" in result.sanitized_text
    assert "Иванов И. И." not in str(result.candidate_data["problem_summary"])
    assert result.redaction_counts["initials_name"] == 1
