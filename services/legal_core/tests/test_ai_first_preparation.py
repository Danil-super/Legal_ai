"""AIF01 safety characterization: urgent routing is not a final AI report.

These existing-boundary checks intentionally do not demand a synchronously ready
AI draft from intermediate_result. The agreed workflow permits asynchronous draft
preparation; its eventual output needs AIF02's reviewed contract before a truthful
acceptance test can assert it. No nonexistent API or storage table is invented here.
"""

from uuid import UUID

import pytest

from legal_core.case_api import ApiError
from legal_core.case_preparation import intermediate_result
from legal_core.case_preparation_contracts import PreparationSource
from legal_core.risk_engine import RiskLevel, RiskPolicy, evaluate_risk
from test_factual_safety_risk import facts

CASE = UUID("00000000-0000-0000-0000-000000000021")
OPERATION = UUID("00000000-0000-0000-0000-000000000022")
ESCALATION = UUID("00000000-0000-0000-0000-000000000023")
POLICY = RiskPolicy("dental-risk.v4", 1_000_000, True, factual_safety_intake_enabled=True)


def test_five_thousand_rubles_alone_does_not_require_high_routing():
    risk = evaluate_risk(facts(moneyRequested="YES", amount={
        "amountKopecks": 500_000, "currency": "RUB",
    }), policy=POLICY, evidence_verified=True)
    assert risk.level == RiskLevel.MEDIUM


@pytest.mark.parametrize("signal,expected", [
    ("writtenRequirementsReceived", RiskLevel.HIGH),
    ("hospitalizationReported", RiskLevel.CRITICAL),
])
def test_current_urgent_route_remains_immediate_and_is_not_mislabeled_as_an_approved_report(
    signal, expected,
):
    risk = evaluate_risk(facts(**{signal: "YES"}), policy=POLICY, evidence_verified=True)
    result = intermediate_result(
        source=PreparationSource(operationId=OPERATION, caseId=CASE,
                                 factSnapshotSha256=risk.fact_snapshot_sha256, context=None),
        risk=risk, escalation_id=ESCALATION, clarification_requests=[], analysis_allowed=True,
    )
    assert risk.level == expected
    assert result.root.status == "HUMAN_REVIEW" and result.root.escalation_id == ESCALATION
    wire = result.model_dump(mode="json", by_alias=True)
    assert not wire.get("analysisAllowed", False) and "report" not in wire
    assert risk.external_draft_allowed is False


def test_missing_escalation_is_not_hidden_by_the_future_ai_draft_path():
    risk = evaluate_risk(facts(hospitalizationReported="YES"), policy=POLICY,
                         evidence_verified=True)
    with pytest.raises(ApiError) as failure:
        intermediate_result(
            source=PreparationSource(operationId=OPERATION, caseId=CASE,
                                     factSnapshotSha256=risk.fact_snapshot_sha256, context=None),
            risk=risk, escalation_id=None, clarification_requests=[], analysis_allowed=True,
        )
    assert failure.value.code == "PREPARATION_ESCALATION_MISSING"
