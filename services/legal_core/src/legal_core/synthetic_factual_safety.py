"""Authored factual screening regressions executed before human policy approval."""

from legal_core.contracts import FactKey
from legal_core.factual_safety_intake import SCREENING_VERSION
from legal_core.factual_safety_risk import SCREENING_SIGNALS
from legal_core.risk_engine import RiskLevel, RiskPolicy, evaluate_early_triage, evaluate_risk


def assert_factual_safety_regressions(policy: RiskPolicy) -> None:
    if not policy.factual_safety_intake_enabled:
        raise ValueError("factual gate requires an independently reviewed capability")
    screening: dict[str, object] = {
        "schemaVersion": SCREENING_VERSION,
        **dict.fromkeys(SCREENING_SIGNALS, "NO"), "amount": "NOT_REQUESTED",
    }
    base: dict[FactKey, object] = {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.INCOMING_COMMUNICATION: "COMPLAINT",
        FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["NO_KNOWN_INFORMATION"],
    }
    for patch, level in (
        ({}, RiskLevel.LOW),
        ({"moneyRequested": "YES", "amount": {
            "amountKopecks": policy.high_demand_threshold_kopecks - 1, "currency": "RUB",
        }}, RiskLevel.MEDIUM),
        ({"moneyRequested": "YES", "amount": {
            "amountKopecks": policy.high_demand_threshold_kopecks, "currency": "RUB",
        }}, RiskLevel.HIGH),
        ({"healthDeteriorationReported": "YES"}, RiskLevel.HIGH),
        ({"hospitalizationReported": "YES"}, RiskLevel.CRITICAL),
        ({"authorityOrCourtDocumentReceived": "YES"}, RiskLevel.CRITICAL),
        ({"representativeContact": "UNKNOWN"}, RiskLevel.UNAVAILABLE),
    ):
        facts = base | {FactKey.FACTUAL_SAFETY_SCREENING: screening | patch}
        result = evaluate_risk(facts, policy=policy, evidence_verified=True)
        if result.level is not level or (
            level is not RiskLevel.LOW and result.external_draft_allowed
        ):
            raise ValueError("factual screening gate failed")
        if level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            early = evaluate_early_triage(facts, policy=policy)
            if early is None or early.level is not level:
                raise ValueError("factual urgent routing gate failed")
    conflict = base | {
        FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["HOSPITALIZATION"],
        FactKey.FACTUAL_SAFETY_SCREENING: screening,
    }
    if (
        evaluate_risk(conflict, policy=policy, evidence_verified=True).level
        is not RiskLevel.UNAVAILABLE
    ):
        raise ValueError("factual contradictory reports gate failed")
