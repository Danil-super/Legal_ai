"""Candidate risk signals from confirmed reports; no legal qualification or inference."""

from collections.abc import Mapping

from pydantic import ValidationError

from legal_core.contracts import FactKey
from legal_core.factual_safety_intake import FactualSafetyScreening, RequestedMoney

SCREENING_SIGNALS = (
    "healthDeteriorationReported", "hospitalizationReported", "representativeContact",
    "writtenRequirementsReceived", "authorityOrCourtDocumentReceived", "authorityReferralMentioned",
    "moneyRequested",
)


def confirmed_screening(facts: Mapping[FactKey, object]) -> FactualSafetyScreening | None:
    try:
        return FactualSafetyScreening.model_validate(facts.get(FactKey.FACTUAL_SAFETY_SCREENING))
    except ValidationError:
        return None


def screening_blocker(
    facts: Mapping[FactKey, object], screening: FactualSafetyScreening | None,
) -> str | None:
    if screening is None:
        return "FACTUAL_SAFETY_SCREENING_UNKNOWN"
    for key in (FactKey.HARM_CLAIMED, FactKey.HOSPITALIZATION, FactKey.LAWYER_CONTACT,
                FactKey.FORMAL_CLAIM, FactKey.REGULATOR_OR_COURT, FactKey.REGULATOR_THREAT):
        if key in facts:
            value = facts[key]
            if type(value) is not bool and not (
                isinstance(value, str) and value in {"YES", "NO", "UNKNOWN"}
            ):
                return "FACTUAL_SAFETY_SCREENING_INVALID"
    data = screening.model_dump(by_alias=True)
    health = facts.get(FactKey.HEALTH_CONSEQUENCE_SIGNALS)
    positives = {
        "hospitalizationReported": (
            isinstance(health, list) and "HOSPITALIZATION" in health
        ) or facts.get(FactKey.HOSPITALIZATION) in (True, "YES"),
        "healthDeteriorationReported": (
            isinstance(health, list) and "COMPLICATION_OR_WORSENING" in health
        ) or facts.get(FactKey.HARM_CLAIMED) in (True, "YES"),
        "authorityOrCourtDocumentReceived": (
            facts.get(FactKey.INCOMING_COMMUNICATION) == "AUTHORITY_OR_COURT_DOCUMENT"
        ) or facts.get(FactKey.REGULATOR_OR_COURT) in (True, "YES"),
        "representativeContact": facts.get(FactKey.LAWYER_CONTACT) in (True, "YES"),
        "writtenRequirementsReceived": facts.get(FactKey.FORMAL_CLAIM) in (True, "YES"),
        "authorityReferralMentioned": facts.get(FactKey.REGULATOR_THREAT) in (True, "YES"),
        "moneyRequested": isinstance(facts.get(FactKey.DEMAND_AMOUNT), dict),
    }
    if any(positive and data[field] == "NO" for field, positive in positives.items()):
        return "FACTUAL_SAFETY_SCREENING_CONFLICT"
    if (
        isinstance(screening.amount, RequestedMoney)
        and FactKey.DEMAND_AMOUNT in facts
        and facts[FactKey.DEMAND_AMOUNT] != screening.amount.model_dump(by_alias=True)
    ):
        return "FACTUAL_SAFETY_SCREENING_CONFLICT"
    for field in SCREENING_SIGNALS:
        if data[field] == "UNKNOWN":
            return f"FACTUAL_SAFETY_{field.upper()}_UNKNOWN"
    if screening.amount == "UNKNOWN":
        return "FACTUAL_SAFETY_AMOUNT_UNKNOWN"
    return None


def screening_high_reasons(screening: FactualSafetyScreening | None) -> tuple[str, ...]:
    if screening is None:
        return ()
    reasons = [reason for value, reason in (
        (screening.health_deterioration_reported, "HEALTH_DETERIORATION_REPORTED"),
        (screening.representative_contact, "LAWYER_OR_REPRESENTATIVE_CONTACT"),
        (screening.written_requirements_received, "WRITTEN_REQUIREMENTS_REPORTED"),
    ) if value == "YES"]
    if (
        isinstance(screening.amount, RequestedMoney)
        and screening.amount.amount_kopecks >= 5_000_000
    ):
        reasons.append("HIGH_DEMAND_AMOUNT")
    return tuple(reasons)
