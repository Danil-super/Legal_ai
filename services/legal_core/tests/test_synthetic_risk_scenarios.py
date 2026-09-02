import pytest

from legal_core.risk_engine import RiskPolicy, evaluate_risk
from legal_core.synthetic_risk_scenarios import (
    assert_p0_synthetic_risk_regressions,
    load_synthetic_risk_scenario_pack,
)


def test_synthetic_risk_pack_is_bounded_and_has_p0_coverage() -> None:
    pack = load_synthetic_risk_scenario_pack()

    assert len(pack.scenarios) == 20
    assert len({scenario.scenario_id for scenario in pack.scenarios}) == 20
    assert {"P0", "P1"} <= {scenario.priority for scenario in pack.scenarios}
    assert all("http" not in scenario.scenario_id for scenario in pack.scenarios)


def test_synthetic_risk_scenarios_lock_deterministic_engine_outcomes() -> None:
    pack = load_synthetic_risk_scenario_pack()

    for scenario in pack.scenarios:
        actual = evaluate_risk(
            scenario.facts,
            policy=pack.policy,
            evidence_verified=scenario.evidence_verified,
        )

        assert actual.level is scenario.expected_level, scenario.scenario_id
        assert actual.reason_codes == scenario.expected_reason_codes, scenario.scenario_id
        assert actual.external_draft_allowed is scenario.expected_external_draft_allowed


def test_p0_synthetic_risk_regressions_reject_incompatible_policy() -> None:
    assert_p0_synthetic_risk_regressions(
        RiskPolicy(version="dental-risk.v1", high_demand_threshold_kopecks=10_000_000)
    )

    with pytest.raises(ValueError, match="risk-medium-compensation-below-threshold"):
        assert_p0_synthetic_risk_regressions(
            RiskPolicy(version="dental-risk.v1", high_demand_threshold_kopecks=9_999_900)
        )
