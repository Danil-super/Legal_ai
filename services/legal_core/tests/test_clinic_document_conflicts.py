import pytest

from legal_core.clinic_document_conflicts import detect_potential_clinic_document_conflicts


@pytest.mark.parametrize(
    ("text", "reason_code"),
    [
        (
            "Возврат денежных средств не осуществляется ни при каких обстоятельствах.",
            "ABSOLUTE_NO_REFUND",
        ),
        (
            "Клиника не несет никакой ответственности за результат лечения.",
            "ABSOLUTE_NO_LIABILITY",
        ),
        (
            "Пациент полностью отказывается от любых претензий к клинике.",
            "WAIVER_OF_ALL_CLAIMS",
        ),
        (
            "Условия гарантии имеют приоритет над правами пациента по закону.",
            "INTERNAL_RULE_OVERRIDES_MANDATORY_RIGHTS",
        ),
        (
            "Нарушение рекомендаций автоматически освобождает клинику от ответственности.",
            "AUTOMATIC_FAULT_SHIFT_TO_PATIENT",
        ),
    ],
)
def test_absolute_internal_wording_requires_review(text: str, reason_code: str) -> None:
    hints = detect_potential_clinic_document_conflicts(text)

    assert [hint.reason_code for hint in hints] == [reason_code]
    assert all(hint.review_required for hint in hints)


def test_safe_non_absolute_wording_does_not_trigger_conflict_hint() -> None:
    text = (
        "Сам факт требования возврата денежных средств не означает автоматического признания "
        "недостатка услуги или вины клиники. Условия внутреннего документа проверяются с учётом "
        "обязательных норм права."
    )

    assert detect_potential_clinic_document_conflicts(text) == ()


def test_multiple_absolute_rules_return_stable_order_without_raw_excerpt() -> None:
    text = (
        "Пациент отказывается от всех претензий. "
        "Клиника не несет ответственности за результат лечения."
    )

    hints = detect_potential_clinic_document_conflicts(text)

    assert [hint.reason_code for hint in hints] == [
        "ABSOLUTE_NO_LIABILITY",
        "WAIVER_OF_ALL_CLAIMS",
    ]
    assert all(not hasattr(hint, "matched_text") for hint in hints)
