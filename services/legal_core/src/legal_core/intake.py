"""Deterministic missing-facts rules for the administrator intake."""

from collections.abc import Mapping

from legal_core.contracts import FactKey, MissingFact, MissingFactSeverity

_ALWAYS_REQUIRED: tuple[tuple[FactKey, str], ...] = (
    (FactKey.INCIDENT_TYPES, "incident"),
    (FactKey.SERVICE_TYPE, "service_type"),
    (FactKey.SERVICE_DATE, "service_date"),
    (FactKey.INCIDENT_DATE, "incident_date"),
    (FactKey.CLAIM_DATE, "claim_date"),
    (FactKey.PROBLEM_SUMMARY, "problem_summary"),
    (FactKey.PATIENT_DEMAND, "patient_demand"),
    (FactKey.FORMAL_CLAIM, "formal_claim"),
    (FactKey.HARM_CLAIMED, "harm_claimed"),
    (FactKey.LAWYER_CONTACT, "lawyer_contact"),
    (FactKey.REGULATOR_OR_COURT, "regulator_or_court"),
    (FactKey.REGULATOR_THREAT, "regulator_threat"),
    (FactKey.CLINIC_DOCUMENTS, "documents"),
)


def _missing(fact_key: FactKey, question_id: str, reason_code: str) -> MissingFact:
    return MissingFact(
        factKey=fact_key,
        severity=MissingFactSeverity.CRITICAL,
        reasonCode=reason_code,
        questionId=question_id,
    )


def _is_absent(facts: Mapping[FactKey, object], key: FactKey) -> bool:
    return key not in facts or facts[key] is None or facts[key] == ""


def _contains(value: object, expected: str) -> bool:
    return isinstance(value, (list, tuple, set)) and expected in value


def missing_facts_for(facts: Mapping[FactKey, object]) -> list[MissingFact]:
    """Return required questions in stable conversational order."""

    missing = [
        _missing(key, question_id, f"{key.value}_REQUIRED")
        for key, question_id in _ALWAYS_REQUIRED
        if _is_absent(facts, key)
    ]

    demands = facts.get(FactKey.PATIENT_DEMAND)
    if (
        _contains(demands, "REFUND_DEMAND")
        or _contains(demands, "COMPENSATION_DEMAND")
    ) and _is_absent(facts, FactKey.DEMAND_AMOUNT):
        missing.append(
            _missing(
                FactKey.DEMAND_AMOUNT,
                "demand_amount",
                "MONEY_DEMAND_REQUIRES_AMOUNT",
            )
        )

    if facts.get(FactKey.FORMAL_CLAIM) is True:
        if _is_absent(facts, FactKey.CLAIM_RECEIVED_AT):
            missing.append(
                _missing(
                    FactKey.CLAIM_RECEIVED_AT,
                    "claim_received_at",
                    "FORMAL_CLAIM_REQUIRES_RECEIVED_DATE",
                )
            )
        if _is_absent(facts, FactKey.RESPONSE_DEADLINE):
            missing.append(
                _missing(
                    FactKey.RESPONSE_DEADLINE,
                    "claim_deadline",
                    "FORMAL_CLAIM_REQUIRES_DEADLINE",
                )
            )

    if facts.get(FactKey.HARM_CLAIMED) in (True, "YES", "UNKNOWN") and _is_absent(
        facts, FactKey.HOSPITALIZATION
    ):
        missing.append(
            _missing(
                FactKey.HOSPITALIZATION,
                "hospitalization",
                "HARM_SIGNAL_REQUIRES_HOSPITALISATION_STATUS",
            )
        )

    if facts.get(FactKey.LAWYER_CONTACT) in (True, "YES"):
        if _is_absent(facts, FactKey.REPRESENTATIVE_AUTHORITY):
            missing.append(
                _missing(
                    FactKey.REPRESENTATIVE_AUTHORITY,
                    "representative_authority",
                    "LAWYER_CONTACT_REQUIRES_AUTHORITY",
                )
            )
        if _is_absent(facts, FactKey.RESPONSE_DEADLINE):
            missing.append(
                _missing(
                    FactKey.RESPONSE_DEADLINE,
                    "representative_deadline",
                    "LAWYER_CONTACT_REQUIRES_DEADLINE",
                )
            )

    if facts.get(FactKey.REGULATOR_OR_COURT) in (True, "YES"):
        for key, question_id in (
            (FactKey.AUTHORITY_KIND, "authority_kind"),
            (FactKey.DOCUMENT_DATE, "authority_document_date"),
            (FactKey.RESPONSE_DEADLINE, "authority_deadline"),
        ):
            if _is_absent(facts, key):
                missing.append(
                    _missing(key, question_id, f"REGULATOR_OR_COURT_REQUIRES_{key.value}")
                )

    return missing
