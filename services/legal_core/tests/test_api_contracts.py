import pytest
from legal_core.api_contracts import AddFactsRequest, FactInput
from pydantic import ValidationError


def test_fact_input_rejects_a_value_type_that_does_not_match_its_fact_key() -> None:
    with pytest.raises(ValidationError, match="INCIDENT_TYPES"):
        FactInput(
            factKey="INCIDENT_TYPES",
            valueType="TEXT",
            value={"text": "Произвольный текст вместо набора типов"},
            sourceType="USER_STATEMENT",
        )


def test_fact_input_rejects_invalid_date_shape_even_when_it_is_not_empty() -> None:
    with pytest.raises(ValidationError, match="SERVICE_DATE"):
        FactInput(
            factKey="SERVICE_DATE",
            valueType="DATE",
            value={"date": "2026-02-30", "precision": "EXACT"},
            sourceType="USER_STATEMENT",
        )


def test_add_facts_request_rejects_duplicate_fact_keys() -> None:
    duplicate_fact = {
        "factKey": "SERVICE_TYPE",
        "valueType": "TEXT",
        "value": {"text": "Профессиональная гигиена"},
        "sourceType": "USER_STATEMENT",
    }

    with pytest.raises(ValidationError, match="fact keys must be unique"):
        AddFactsRequest(
            questionId="service_type",
            intakeSchemaVersion="dental-case-intake.v1",
            facts=[duplicate_fact, duplicate_fact],
        )
