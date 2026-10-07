"""Authored guided-v2 safety gate for human review of the v3 policy."""

from legal_core.contracts import FactKey
from legal_core.risk_engine import RiskLevel, RiskPolicy, evaluate_early_triage, evaluate_risk


def assert_v3_synthetic_risk_regressions(policy: RiskPolicy) -> None:
    if not policy.guided_v2_explicit_signals_enabled:
        raise ValueError("v3 synthetic gate requires explicit guided-v2 capability")
    base: dict[FactKey, object] = {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.INCOMING_COMMUNICATION: "COMPLAINT",
        FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["NO_KNOWN_INFORMATION"],
    }
    scenarios = (
        (
            base | {FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["HOSPITALIZATION"]},
            ("HOSPITALIZATION_REPORTED",),
        ),
        (
            base | {FactKey.INCOMING_COMMUNICATION: "AUTHORITY_OR_COURT_DOCUMENT"},
            ("AUTHORITY_OR_COURT_DOCUMENT_REPORTED",),
        ),
        (
            base
            | {
                FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["HOSPITALIZATION"],
                FactKey.INCOMING_COMMUNICATION: "AUTHORITY_OR_COURT_DOCUMENT",
            },
            ("HOSPITALIZATION_REPORTED", "AUTHORITY_OR_COURT_DOCUMENT_REPORTED"),
        ),
    )
    for facts, reasons in scenarios:
        early = evaluate_early_triage(facts, policy=policy)
        full = evaluate_risk(facts, policy=policy, evidence_verified=False)
        if (
            early is None
            or early.level is not RiskLevel.CRITICAL
            or early.reason_codes != reasons
            or early.external_draft_allowed
            or full.level is not RiskLevel.UNAVAILABLE
            or full.external_draft_allowed
        ):
            raise ValueError("v3 synthetic urgent routing/evidence gate failed")
    for health in (["UNKNOWN"], ["NO_KNOWN_INFORMATION"], ["COMPLICATION_OR_WORSENING"]):
        facts = base | {FactKey.HEALTH_CONSEQUENCE_SIGNALS: health}
        full = evaluate_risk(facts, policy=policy, evidence_verified=True)
        if (
            evaluate_early_triage(facts, policy=policy) is not None
            or full.level is not RiskLevel.UNAVAILABLE
            or full.external_draft_allowed
        ):
            raise ValueError("v3 synthetic unknown-health gate failed")
    for amount, expected in ((4_999_999, None), (5_000_000, RiskLevel.HIGH)):
        facts = base | {FactKey.DEMAND_AMOUNT: {"amountKopecks": amount, "currency": "RUB"}}
        early = evaluate_early_triage(facts, policy=policy)
        if (None if early is None else early.level) != expected:
            raise ValueError("v3 synthetic inclusive monetary boundary failed")
