"""V2 facts are explicit and never fall back to the legal-label questionnaire."""

import pytest
from pydantic import ValidationError

from legal_core.api_contracts import FactInput, TelegramWorkflowSubmissionRequest
from legal_core.contracts import FactKey
from legal_core.intake import missing_facts_for


def _v2_facts() -> dict[FactKey, object]:
    return {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.INCOMING_COMMUNICATION: "COMPLAINT",
        FactKey.INCOMING_SOURCE_STATUS: "NOT_ATTACHED",
        FactKey.SITUATION_AREAS: ["TREATMENT", "SERVICE"],
        FactKey.AFFECTED_SERVICES: ["терапевтическое лечение"],
        FactKey.EVENT_SUMMARY: "После лечения клиент сообщил о дискомфорте.",
        FactKey.EVENT_DATE: {"date": "2026-09-01", "precision": "EXACT"},
        FactKey.CONFLICT_STAGE: "ONGOING",
        FactKey.CLINIC_ACTIONS: ["INVITED_FOR_EXAMINATION"],
        FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["UNKNOWN"],
        FactKey.CASE_MATERIALS_STATUS: "NOT_ATTACHED",
    }


def test_complete_guided_v2_intake_does_not_require_legacy_legal_labels() -> None:
    assert missing_facts_for(_v2_facts()) == []


def test_guided_v2_unknown_date_returns_only_a_concrete_date_follow_up() -> None:
    facts = _v2_facts()
    facts[FactKey.EVENT_DATE] = {"date": None, "precision": "UNKNOWN"}

    missing = missing_facts_for(facts)

    assert [(item.fact_key, item.question_id, item.reason_code) for item in missing] == [
        (
            FactKey.EVENT_DATE,
            "event_date",
            "EVENT_DATE_REQUIRES_EXACT_DATE_FOR_TIME_DEPENDENT_ANALYSIS",
        )
    ]


def test_treatment_requires_service_description_but_other_topics_do_not() -> None:
    facts = _v2_facts()
    del facts[FactKey.AFFECTED_SERVICES]
    assert [item.fact_key for item in missing_facts_for(facts)] == [FactKey.AFFECTED_SERVICES]

    facts[FactKey.SITUATION_AREAS] = ["PERSONAL_DATA"]
    assert missing_facts_for(facts) == []


def test_v2_contract_accepts_only_bounded_typed_facts() -> None:
    input_fact = FactInput.model_validate(
        {
            "factKey": "AFFECTED_SERVICES",
            "valueType": "TEXT_LIST",
            "value": {"items": ["терапевтическое лечение", "осмотр"]},
            "sourceType": "USER_STATEMENT",
        }
    )
    assert input_fact.fact_key is FactKey.AFFECTED_SERVICES

    with pytest.raises(ValidationError, match="AFFECTED_SERVICES"):
        FactInput.model_validate(
            {
                "factKey": "AFFECTED_SERVICES",
                "valueType": "TEXT",
                "value": {"text": "осмотр"},
                "sourceType": "USER_STATEMENT",
            }
        )


def test_v2_telegram_workflow_contract_is_versioned() -> None:
    request = TelegramWorkflowSubmissionRequest.model_validate(
        {
            "intakeSchemaVersion": "dental-case-intake.v2",
            "facts": [
                {
                    "factKey": "INTAKE_VERSION",
                    "valueType": "ENUM",
                    "value": {"value": "GUIDED_V2"},
                    "sourceType": "USER_STATEMENT",
                }
            ],
        }
    )
    assert request.intake_schema_version == "dental-case-intake.v2"
