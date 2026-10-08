"""Owner-requested 10,000 RUB boundary; no patient data or live approvals."""

import pytest
from pydantic import ValidationError

from legal_core.contracts import FactKey
from legal_core.risk_engine import RiskLevel, RiskPolicy, evaluate_early_triage, evaluate_risk
from legal_core.risk_policy_approval import RiskPolicyApproval, policy_payload
from legal_core.synthetic_factual_safety import assert_factual_safety_regressions
from legal_core.synthetic_risk_v3 import assert_v3_synthetic_risk_regressions
from test_factual_safety_risk import facts
from test_risk_engine import _complete_safe_facts
from test_risk_policy_v3 import load_policy


def v4_policy():
    return RiskPolicy("dental-risk.v4", 1_000_000, True, factual_safety_intake_enabled=True)


def v4_approval(**overrides):
    return RiskPolicyApproval(
        **{
            "reviewer_telegram_user_id": 7_000_000_001,
            "version": 4,
            "high_demand_threshold_kopecks": 1_000_000,
            "incident_triggers_reviewed": True,
            "monetary_threshold_reviewed": True,
            "escalation_rules_reviewed": True,
            "early_triage_enabled": True,
            "supersede_approved": True,
            "guided_v2_explicit_signals_enabled": True,
            "direct_v1_supersession_reviewed": True,
            "factual_safety_intake_enabled": True,
            "factual_safety_intake_reviewed": True,
            **overrides,
        }
    )


def v4_payload():
    return {
        "schemaVersion": "risk-policy.v4",
        "highDemandThresholdKopecks": 1_000_000,
        "earlyTriageEnabled": True,
        "guidedV2ExplicitSignalsEnabled": True,
        "factualSafetyIntakeVersion": "factual-safety-intake.v1",
    }


@pytest.mark.parametrize("amount", [999_900, 999_999, 1_000_000, 1_000_001, 1_000_100])
@pytest.mark.parametrize("representation", ["screening", "legacy"])
def test_v4_inclusive_amount_routes_both_confirmed_representations(amount, representation):
    data = facts(moneyRequested="YES", amount={"amountKopecks": amount, "currency": "RUB"})
    if representation == "legacy":
        data = _complete_safe_facts() | {
            FactKey.PATIENT_DEMAND: ["COMPENSATION_DEMAND"],
            FactKey.DEMAND_AMOUNT: {"amountKopecks": amount, "currency": "RUB"},
        }
    policy = v4_policy()
    early = evaluate_early_triage(data, policy=policy)
    result = evaluate_risk(data, policy=policy, evidence_verified=True)
    if amount >= 1_000_000:
        assert early is not None and early.level is RiskLevel.HIGH
        assert early.reason_codes == ("HIGH_DEMAND_AMOUNT",)
        assert result.level is RiskLevel.HIGH
    else:
        assert early is None
        assert result.level is RiskLevel.MEDIUM
    assert result.external_draft_allowed is False
    assert (
        evaluate_risk(data, policy=policy, evidence_verified=False).level is RiskLevel.UNAVAILABLE
    )


@pytest.mark.parametrize("signal", ["hospitalizationReported", "authorityOrCourtDocumentReceived"])
def test_v4_critical_is_not_restricted_to_monetary_cases(signal):
    data = facts(**{signal: "YES"})
    assert evaluate_early_triage(data, policy=v4_policy()).level is RiskLevel.CRITICAL
    assert (
        evaluate_risk(data, policy=v4_policy(), evidence_verified=True).level is RiskLevel.CRITICAL
    )


def test_v4_unknown_amount_is_not_inferred_from_narrative():
    data = facts(moneyRequested="YES", amount="UNKNOWN")
    data[FactKey.EVENT_SUMMARY] = "Synthetic patient requests 10000 rubles."
    assert evaluate_early_triage(data, policy=v4_policy()) is None
    result = evaluate_risk(data, policy=v4_policy(), evidence_verified=True)
    assert result.level is RiskLevel.UNAVAILABLE
    assert result.reason_codes == ("FACTUAL_SAFETY_AMOUNT_UNKNOWN",)


def test_v4_has_explicit_hash_covered_capabilities_and_runs_all_authored_gates():
    payload = policy_payload(v4_approval())
    assert payload == v4_payload()
    approved = load_policy(payload, version=4)
    assert approved.early_triage_enabled is True
    assert approved.domain == v4_policy()
    assert_v3_synthetic_risk_regressions(approved.domain)
    assert_factual_safety_regressions(approved.domain)


@pytest.mark.parametrize(
    "mutation",
    [
        {"highDemandThresholdKopecks": 5_000_000},
        {"highDemandThresholdKopecks": True},
        {"guidedV2ExplicitSignalsEnabled": False},
        {"earlyTriageEnabled": False},
        {"factualSafetyIntakeVersion": "factual-safety-intake.v2"},
        {"schemaVersion": "risk-policy.v3"},
        {"unexpected": True},
    ],
)
def test_v4_repository_rejects_altered_contract(mutation):
    with pytest.raises(ValueError):
        load_policy(v4_payload() | mutation, version=4)


def test_v4_repository_rejects_legacy_schema_wrong_version_and_tampered_hash():
    with pytest.raises(ValueError):
        load_policy(
            {"schemaVersion": "risk-policy.v1", "highDemandThresholdKopecks": 1_000_000}, version=4
        )
    with pytest.raises(ValueError):
        load_policy(v4_payload(), version=3)
    with pytest.raises(ValueError):
        load_policy(v4_payload(), version=4, digest="0" * 64)


@pytest.mark.parametrize(
    "mutation",
    [
        {"high_demand_threshold_kopecks": 5_000_000},
        {"early_triage_enabled": False},
        {"supersede_approved": False},
        {"monetary_threshold_reviewed": False},
        {"direct_v1_supersession_reviewed": False},
        {"factual_safety_intake_reviewed": False},
        {"version": 5},
    ],
)
def test_v4_approval_rejects_unreviewed_or_changed_contract(mutation):
    with pytest.raises(ValidationError):
        v4_approval(**mutation)


def test_v4_does_not_change_the_v3_factual_boundary():
    policy = RiskPolicy("dental-risk.v3", 5_000_000, True, factual_safety_intake_enabled=True)
    data = facts(moneyRequested="YES", amount={"amountKopecks": 1_000_000, "currency": "RUB"})
    assert evaluate_risk(data, policy=policy, evidence_verified=True).level is RiskLevel.MEDIUM
    assert evaluate_early_triage(data, policy=policy) is None
