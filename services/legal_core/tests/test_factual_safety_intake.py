"""Explicit user reports, never legal labels or inferred negative answers."""

import pytest
from pydantic import ValidationError

from legal_core.api_contracts import FactInput


def screening(**overrides):
    return {
        "schemaVersion": "factual-safety-intake.v1",
        "healthDeteriorationReported": "NO", "hospitalizationReported": "NO",
        "representativeContact": "NO", "writtenRequirementsReceived": "NO",
        "authorityOrCourtDocumentReceived": "NO", "authorityReferralMentioned": "NO",
        "moneyRequested": "NO", "amount": "NOT_REQUESTED", **overrides,
    }


def test_factual_screening_is_validated_and_round_trips_as_a_new_versioned_fact():
    fact = FactInput.model_validate({
        "factKey": "FACTUAL_SAFETY_SCREENING", "valueType": "JSON",
        "value": screening(), "sourceType": "USER_STATEMENT",
    })
    assert fact.value == screening()


@pytest.mark.parametrize("mutation", [
    {"hospitalizationReported": False}, {"healthDeteriorationReported": "NO_KNOWN_INFORMATION"},
    {"schemaVersion": "factual-safety-intake.v2"}, {"unreviewed": "NO"},
    {"moneyRequested": "YES", "amount": "NOT_REQUESTED"},
    {"moneyRequested": "NO", "amount": {"amountKopecks": 500_000, "currency": "RUB"}},
    {"moneyRequested": "YES", "amount": {"amountKopecks": True, "currency": "RUB"}},
    {"moneyRequested": "YES", "amount": {"amountKopecks": 1.5, "currency": "RUB"}},
    {"moneyRequested": "YES", "amount": {"amountKopecks": 0, "currency": "RUB"}},
    {"moneyRequested": "YES", "amount": {"amountKopecks": 100, "currency": "USD"}},
])
def test_screening_rejects_inferred_unversioned_or_inexact_answers(mutation):
    from legal_core.factual_safety_intake import FactualSafetyScreening
    with pytest.raises(ValidationError):
        FactualSafetyScreening.model_validate(screening(**mutation))


def test_screening_requires_every_confirmed_answer_but_preserves_unknown():
    from legal_core.factual_safety_intake import FactualSafetyScreening
    value = screening(hospitalizationReported="UNKNOWN", moneyRequested="UNKNOWN", amount="UNKNOWN")
    assert FactualSafetyScreening.model_validate(value).model_dump(by_alias=True) == value
    del value["hospitalizationReported"]
    with pytest.raises(ValidationError):
        FactualSafetyScreening.model_validate(value)
