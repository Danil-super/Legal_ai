from datetime import date

from legal_core.contracts import FactKey, MissingFactSeverity
from legal_core.intake import missing_facts_for


def complete_synthetic_facts() -> dict[FactKey, object]:
    return {
        FactKey.INCIDENT_TYPES: ["CROWN_PROBLEM", "REFUND_DEMAND"],
        FactKey.PRIMARY_INCIDENT_TYPE: "CROWN_PROBLEM",
        FactKey.SERVICE_TYPE: "Установка коронки",
        FactKey.SERVICE_DATE: {"date": date(2026, 6, 1), "precision": "EXACT"},
        FactKey.INCIDENT_DATE: {"date": date(2026, 7, 1), "precision": "EXACT"},
        FactKey.CLAIM_DATE: {"date": date(2026, 7, 2), "precision": "EXACT"},
        FactKey.PROBLEM_SUMMARY: "После услуги пациент сообщил о сколе конструкции.",
        FactKey.PATIENT_DEMAND: ["REFUND_DEMAND"],
        FactKey.DEMAND_AMOUNT: {
            "amountMinor": 150_000,
            "currency": "RUB",
            "isExact": True,
        },
        FactKey.FORMAL_CLAIM: False,
        FactKey.HARM_CLAIMED: False,
        FactKey.LAWYER_CONTACT: False,
        FactKey.REGULATOR_OR_COURT: False,
        FactKey.REGULATOR_THREAT: False,
        FactKey.CLINIC_DOCUMENTS: {"CONTRACT": "AVAILABLE"},
    }


def test_complete_intake_has_no_missing_facts() -> None:
    assert missing_facts_for(complete_synthetic_facts()) == []


def test_money_demand_requires_integer_minor_amount() -> None:
    facts = complete_synthetic_facts()
    del facts[FactKey.DEMAND_AMOUNT]

    missing = missing_facts_for(facts)

    assert [item.fact_key for item in missing] == [FactKey.DEMAND_AMOUNT]
    assert missing[0].severity is MissingFactSeverity.CRITICAL
    assert missing[0].question_id == "demand_amount"


def test_formal_claim_requires_received_date_and_deadline() -> None:
    facts = complete_synthetic_facts()
    facts[FactKey.FORMAL_CLAIM] = True

    missing = missing_facts_for(facts)

    assert {item.fact_key for item in missing} == {
        FactKey.CLAIM_RECEIVED_AT,
        FactKey.RESPONSE_DEADLINE,
    }


def test_unknown_harm_signal_requires_hospitalisation_answer() -> None:
    facts = complete_synthetic_facts()
    facts[FactKey.HARM_CLAIMED] = "UNKNOWN"

    missing = missing_facts_for(facts)

    assert [item.fact_key for item in missing] == [FactKey.HOSPITALIZATION]


def test_lawyer_contact_requires_authority_and_a_deadline() -> None:
    facts = complete_synthetic_facts()
    facts[FactKey.LAWYER_CONTACT] = "YES"

    missing = missing_facts_for(facts)

    assert {item.fact_key for item in missing} == {
        FactKey.REPRESENTATIVE_AUTHORITY,
        FactKey.RESPONSE_DEADLINE,
    }
