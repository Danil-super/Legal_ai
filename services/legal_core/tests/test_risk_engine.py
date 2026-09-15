from legal_core.contracts import FactKey
from legal_core.risk_engine import RiskLevel, RiskPolicy, evaluate_risk


def test_early_triage_routes_known_emergency_even_with_unknown_other_signals() -> None:
    from legal_core.risk_engine import evaluate_early_triage

    policy = RiskPolicy(version="dental-risk.v2", high_demand_threshold_kopecks=5_000_000)
    result = evaluate_early_triage({FactKey.HOSPITALIZATION: "YES"}, policy=policy)
    assert result is not None and result.level is RiskLevel.CRITICAL
    assert result.external_draft_allowed is False
    assert evaluate_early_triage({}, policy=policy) is None
    result = evaluate_early_triage(
        {FactKey.DEMAND_AMOUNT: {"amountKopecks": 5_000_000, "currency": "RUB"}}, policy=policy
    )
    assert result is not None and result.level is RiskLevel.HIGH


def _complete_safe_facts() -> dict[FactKey, object]:
    return {
        FactKey.HARM_CLAIMED: "NO",
        FactKey.HOSPITALIZATION: "NO",
        FactKey.LAWYER_CONTACT: "NO",
        FactKey.FORMAL_CLAIM: "NO",
        FactKey.REGULATOR_OR_COURT: "NO",
        FactKey.REGULATOR_THREAT: "NO",
        FactKey.PATIENT_DEMAND: ["AESTHETIC_DISSATISFACTION"],
        FactKey.CLINIC_DOCUMENTS: {
            "CONTRACT": "AVAILABLE",
            "MEDICAL_RECORD": "AVAILABLE",
            "INFORMED_CONSENT": "AVAILABLE",
        },
    }


def test_hospitalisation_is_critical_and_has_stable_traceability() -> None:
    facts = _complete_safe_facts() | {FactKey.HOSPITALIZATION: "YES"}

    assessment = evaluate_risk(
        facts,
        policy=RiskPolicy(version="risk-policy.v1", high_demand_threshold_kopecks=10_000_000),
        evidence_verified=True,
    )

    assert assessment.level is RiskLevel.CRITICAL
    assert assessment.reason_codes == ("HOSPITALIZATION_REPORTED",)
    assert assessment.policy_version == "risk-policy.v1"
    assert len(assessment.fact_snapshot_sha256) == 64
    assert assessment.external_draft_allowed is False


def test_formal_claim_is_high_and_blocks_unreviewed_external_draft() -> None:
    facts = _complete_safe_facts() | {FactKey.FORMAL_CLAIM: "YES"}

    assessment = evaluate_risk(
        facts,
        policy=RiskPolicy(version="risk-policy.v1", high_demand_threshold_kopecks=10_000_000),
        evidence_verified=True,
    )

    assert assessment.level is RiskLevel.HIGH
    assert assessment.reason_codes == ("FORMAL_CLAIM_RECEIVED",)
    assert assessment.external_draft_allowed is False


def test_unverified_evidence_fails_closed_before_any_risk_conclusion() -> None:
    assessment = evaluate_risk(
        _complete_safe_facts(),
        policy=RiskPolicy(version="risk-policy.v1", high_demand_threshold_kopecks=10_000_000),
        evidence_verified=False,
    )

    assert assessment.level is RiskLevel.UNAVAILABLE
    assert assessment.reason_codes == ("EVIDENCE_NOT_VERIFIED",)
    assert assessment.external_draft_allowed is False


def test_unknown_required_signal_fails_closed() -> None:
    facts = _complete_safe_facts() | {FactKey.REGULATOR_OR_COURT: "UNKNOWN"}

    assessment = evaluate_risk(
        facts,
        policy=RiskPolicy(version="risk-policy.v1", high_demand_threshold_kopecks=10_000_000),
        evidence_verified=True,
    )

    assert assessment.level is RiskLevel.UNAVAILABLE
    assert assessment.reason_codes == ("REGULATOR_OR_COURT_UNKNOWN",)


def test_high_demand_is_inclusive_of_the_policy_threshold() -> None:
    facts = _complete_safe_facts() | {
        FactKey.PATIENT_DEMAND: ["COMPENSATION_DEMAND"],
        FactKey.DEMAND_AMOUNT: {"amountKopecks": 10_000_000, "currency": "RUB"},
    }

    assessment = evaluate_risk(
        facts,
        policy=RiskPolicy(version="risk-policy.v1", high_demand_threshold_kopecks=10_000_000),
        evidence_verified=True,
    )

    assert assessment.level is RiskLevel.HIGH
    assert assessment.reason_codes == ("HIGH_DEMAND_AMOUNT",)
