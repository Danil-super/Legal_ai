"""Literal money input must not depend on a model successfully encoding kopecks."""

import pytest
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from test_intake_answer_interpretation import DATA, proposed, run_model, unresolved


def amount_data():
    return DATA | {"safetyScreening": {"schemaVersion": SCREENING_VERSION,
        **dict.fromkeys(SCREENING_FIELDS, "NO"), "moneyRequested": "YES"}}


@pytest.mark.parametrize("source,kopecks", [
    ("10000", 1_000_000), ("10 000", 1_000_000), ("10000,50", 1_000_050),
    ("10\u00a0000,50 ₽", 1_000_050), ("12000.05 RUB", 1_200_005),
])
def test_exact_literal_rubles_are_grounded_without_any_model_call(source, kopecks):
    calls = []
    result = run_model(source, unresolved(), state="SAFETY", data=amount_data(), calls=calls)
    assert not calls, "a literal amount must not wait for a fragile model JSON response"
    assert result.status == "PROPOSED"
    assert result.value == {"amountKopecks": kopecks, "currency": "RUB"}
    result.validate_source(source)


@pytest.mark.parametrize("source", [
    "около 10000 рублей", "10000 или 12000", "10000 USD", "-10000", "0", "1.234",
    "Вернуть 10000 рублей", "не требует 10000 рублей", "не знаю",
])
def test_ambiguous_sentences_or_unsupported_numbers_are_not_silently_fast_parsed(source):
    calls = []
    result = run_model(source, unresolved(), state="SAFETY", data=amount_data(), calls=calls)
    assert len(calls) == 1 and result.status == "UNRESOLVED" and result.value is None


def test_literal_number_is_not_an_amount_when_current_question_is_different():
    calls = []
    result = run_model("10000", proposed("10000", "OTHER"), state="INCOMING", calls=calls)
    assert len(calls) == 1 and result.value == "OTHER"


def test_identifier_like_number_still_crosses_the_privacy_guard_before_fast_parsing():
    calls = []
    with pytest.raises(ValueError):
        run_model("1000000001", unresolved(), state="SAFETY", data=amount_data(), calls=calls)
    assert not calls
