"""Independent actual Hermes HTTP envelope probes for numeric source grounding."""

import pytest
from agent_orchestrator.hermes_client import HermesProtocolError
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from test_intake_answer_interpretation import DATA, proposed, run_model


@pytest.mark.parametrize("source,kopecks", [
    ("100 USD", 10000), ("Просит вернуть 100 долларов, не рублей", 1000000),
    ("Просит 100 рублей", 1000000), ("10000 или 12000 рублей", 1000000),
    ("Не требует 10000 рублей", 1000000), ("-10000 рублей", 1000000),
])
def test_valid_model_schema_cannot_bypass_numeric_currency_or_negation_grounding(source, kopecks):
    data = DATA | {"safetyScreening": {"schemaVersion": SCREENING_VERSION,
        **dict.fromkeys(SCREENING_FIELDS, "NO"), "moneyRequested": "YES"}}
    calls = []
    with pytest.raises(HermesProtocolError) as caught:
        run_model(source, proposed(source, {"amountKopecks": kopecks, "currency": "RUB"}),
                  state="SAFETY", data=data, calls=calls)
    assert len(calls) == 1
    assert caught.value.__cause__ is None and caught.value.__context__ is None
