"""Only the explicitly enabled candidate policy may clear factual guided intake."""

import pytest

from legal_core.contracts import FactKey
from legal_core.risk_engine import RiskLevel, RiskPolicy, evaluate_early_triage, evaluate_risk
from test_factual_safety_intake import screening


def policy():
    return RiskPolicy("dental-risk.v3", 5_000_000, True, factual_safety_intake_enabled=True)


def facts(**overrides):
    return {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.INCOMING_COMMUNICATION: "MESSAGE_OR_REQUEST",
        FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["NO_KNOWN_INFORMATION"],
        FactKey.FACTUAL_SAFETY_SCREENING: screening(**overrides),
    }


@pytest.mark.parametrize(
    ("overrides", "level"),
    [
        ({}, RiskLevel.LOW),
        (
            {"moneyRequested": "YES", "amount": {"amountKopecks": 4_999_999, "currency": "RUB"}},
            RiskLevel.MEDIUM,
        ),
        (
            {"moneyRequested": "YES", "amount": {"amountKopecks": 5_000_000, "currency": "RUB"}},
            RiskLevel.HIGH,
        ),
        ({"healthDeteriorationReported": "YES"}, RiskLevel.HIGH),
        ({"representativeContact": "YES"}, RiskLevel.HIGH),
        ({"writtenRequirementsReceived": "YES"}, RiskLevel.HIGH),
        ({"authorityReferralMentioned": "YES"}, RiskLevel.MEDIUM),
        ({"hospitalizationReported": "YES"}, RiskLevel.CRITICAL),
        ({"authorityOrCourtDocumentReceived": "YES"}, RiskLevel.CRITICAL),
    ],
)
def test_explicit_facts_clear_only_their_independently_confirmed_risk_dimensions(overrides, level):
    data = facts(**overrides)
    assessment = evaluate_risk(data, policy=policy(), evidence_verified=True)
    assert assessment.level is level
    assert data[FactKey.HEALTH_CONSEQUENCE_SIGNALS] == ["NO_KNOWN_INFORMATION"]
    assert FactKey.HARM_CLAIMED not in data and FactKey.FORMAL_CLAIM not in data
    urgent = evaluate_early_triage(data, policy=policy())
    assert (urgent is not None) == (level in {RiskLevel.HIGH, RiskLevel.CRITICAL})


@pytest.mark.parametrize(
    "field",
    [
        "healthDeteriorationReported",
        "hospitalizationReported",
        "representativeContact",
        "writtenRequirementsReceived",
        "authorityOrCourtDocumentReceived",
        "authorityReferralMentioned",
        "moneyRequested",
    ],
)
def test_unknown_safety_answers_do_not_clear_a_case(field):
    overrides = {field: "UNKNOWN"}
    if field == "moneyRequested":
        overrides["amount"] = "UNKNOWN"
    result = evaluate_risk(facts(**overrides), policy=policy(), evidence_verified=True)
    assert result.level is RiskLevel.UNAVAILABLE
    from legal_core.risk_engine import risk_missing_facts

    assert risk_missing_facts(result)[0].question_id == "factual_safety_" + field


def test_unknown_monetary_amount_needs_an_exact_follow_up():
    result = evaluate_risk(
        facts(moneyRequested="YES", amount="UNKNOWN"), policy=policy(), evidence_verified=True
    )
    assert result.level is RiskLevel.UNAVAILABLE
    from legal_core.risk_engine import risk_missing_facts

    assert risk_missing_facts(result)[0].question_id == "factual_safety_amount"


def test_missing_envelope_and_historical_policy_never_infer_clearance():
    data = facts()
    old_candidate = RiskPolicy("dental-risk.v3", 5_000_000, True)
    assert (
        evaluate_risk(data, policy=old_candidate, evidence_verified=True).level
        is RiskLevel.UNAVAILABLE
    )
    del data[FactKey.FACTUAL_SAFETY_SCREENING]
    assert (
        evaluate_risk(data, policy=policy(), evidence_verified=True).level is RiskLevel.UNAVAILABLE
    )


def test_urgent_positive_survives_a_contradictory_factual_negative_but_blocks_final_report():
    data = facts()
    data[FactKey.HEALTH_CONSEQUENCE_SIGNALS] = ["HOSPITALIZATION"]
    early = evaluate_early_triage(data, policy=policy())
    assert early is not None and early.level is RiskLevel.CRITICAL
    final = evaluate_risk(data, policy=policy(), evidence_verified=True)
    assert final.level is RiskLevel.UNAVAILABLE
    assert final.reason_codes == ("FACTUAL_SAFETY_SCREENING_CONFLICT",)


def test_early_routing_never_removes_the_evidence_gate():
    data = facts(hospitalizationReported="YES", healthDeteriorationReported="UNKNOWN")
    assert evaluate_early_triage(data, policy=policy()).level is RiskLevel.CRITICAL
    assert (
        evaluate_risk(data, policy=policy(), evidence_verified=False).level is RiskLevel.UNAVAILABLE
    )


@pytest.mark.parametrize("value", [0, 1, [], {}, "INVALID"])
def test_new_envelope_does_not_hide_invalid_independently_recorded_legacy_signals(value):
    data = facts()
    data[FactKey.REGULATOR_THREAT] = value
    assert (
        evaluate_risk(data, policy=policy(), evidence_verified=True).level is RiskLevel.UNAVAILABLE
    )


@pytest.mark.parametrize("incoming", [None, "UNKNOWN", "INVALID"])
def test_safety_envelope_does_not_hide_unknown_incoming_material(incoming):
    data = facts()
    data[FactKey.INCOMING_COMMUNICATION] = incoming
    result = evaluate_risk(data, policy=policy(), evidence_verified=True)
    assert result.level is RiskLevel.UNAVAILABLE
    assert result.reason_codes == ("INCOMING_COMMUNICATION_UNKNOWN",)


def test_different_independently_confirmed_monetary_amounts_require_correction():
    data = facts(moneyRequested="YES", amount={"amountKopecks": 100_000, "currency": "RUB"})
    data[FactKey.DEMAND_AMOUNT] = {"amountKopecks": 5_000_000, "currency": "RUB"}
    assert evaluate_early_triage(data, policy=policy()).level is RiskLevel.HIGH
    final = evaluate_risk(data, policy=policy(), evidence_verified=True)
    assert final.level is RiskLevel.UNAVAILABLE
    assert final.reason_codes == ("FACTUAL_SAFETY_SCREENING_CONFLICT",)
