"""A model cannot silently turn another currency or a different literal amount into RUB."""

import pytest
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_answer_values import apply_answer_value, validate_answer_value


def data():
    return {"intakeVersion": 2, "safetyScreening": {
        "schemaVersion": SCREENING_VERSION, **dict.fromkeys(SCREENING_FIELDS, "NO"),
        "moneyRequested": "YES",
    }}


@pytest.mark.parametrize("source,kopecks", [
    ("10000", 1000000), ("Требует вернуть 10 000 рублей", 1000000),
    ("Просит 10\u00a0000 ₽", 1000000), ("12\u202f000,50 руб.", 1200050),
    ("12000.50 RUB", 1200050), ("Требует 10 тысяч рублей", 1000000),
    ("10 тыс. руб.", 1000000), ("10,5 тысячи рублей", 1050000),
    ("0,01 рубля", 1),
])
def test_only_one_unambiguous_literal_ruble_amount_can_support_a_numeric_proposal(source, kopecks):
    draft = data()
    question = derive_answer_question(draft, "SAFETY")
    value = {"amountKopecks": kopecks, "currency": "RUB"}
    assert validate_answer_value(question, value, source, draft) == value
    assert apply_answer_value(draft, question, value, source)["safetyScreening"]["amount"] == value


@pytest.mark.parametrize("source,kopecks", [
    ("Просит вернуть 100 долларов, не рублей", 1000000),
    ("100 USD", 10000), ("100 EUR", 10000), ("100 евро", 10000),
    ("100 $", 10000), ("100 €", 10000), ("100 юаней", 10000),
    ("100 белорусских рублей", 10000), ("100 BYN", 10000),
    ("Просит 100 рублей", 1000000), ("Просит 10000 рублей", 10000),
    ("Сначала 100 рублей, затем 10000 рублей", 1000000),
    ("10000 или 12000 рублей", 1000000), ("От 10000 до 12000 рублей", 1000000),
    ("Не требует 10000 рублей", 1000000), ("Денег не просил", 1000000),
    ("-10000 рублей", 1000000), ("\u221210000 рублей", 1000000),
    ("10000 рублей не требуется", 1000000), ("около 10000 рублей", 1000000),
    ("10000\u201312000 рублей", 1000000), ("Десять тысяч рублей", 1000000),
    ("10 миллионов рублей", 1000), ("10.000 рублей", 1000),
    ("Номер A10000", 1000000), ("10000 баллов", 1000000),
])
def test_foreign_conflicting_negated_ambiguous_or_unsupported_amount_never_becomes_rubles(
    source, kopecks,
):
    draft = data()
    with pytest.raises(ValueError) as caught:
        validate_answer_value(derive_answer_question(draft, "SAFETY"), {
            "amountKopecks": kopecks, "currency": "RUB",
        }, source, draft)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_explicit_unknown_attempt_does_not_require_inventing_a_number():
    draft = data()
    question = derive_answer_question(draft, "SAFETY")
    assert validate_answer_value(question, "UNKNOWN", "Точную сумму не знаю", draft) == "UNKNOWN"
