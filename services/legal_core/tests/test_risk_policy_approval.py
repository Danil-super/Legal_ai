import hashlib
import json

import pytest
from pydantic import ValidationError

from legal_core.risk_policy_approval import (
    RiskPolicyApproval,
    policy_content_sha256,
    policy_payload,
    rubles_to_kopecks,
)


def _approval(**overrides):
    values = {
        "reviewer_telegram_user_id": 7_000_000_001,
        "version": 1,
        "high_demand_threshold_kopecks": 10_000_000,
        "incident_triggers_reviewed": True,
        "monetary_threshold_reviewed": True,
        "escalation_rules_reviewed": True,
    }
    values.update(overrides)
    return RiskPolicyApproval(**values)


def test_policy_payload_matches_runtime_v1_schema() -> None:
    payload = policy_payload(_approval())

    assert payload == {
        "schemaVersion": "risk-policy.v1",
        "highDemandThresholdKopecks": 10_000_000,
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert policy_content_sha256(payload) == expected


def test_policy_approval_requires_all_human_review_flags() -> None:
    with pytest.raises(ValidationError, match="review attestations"):
        _approval(escalation_rules_reviewed=False)


@pytest.mark.parametrize(
    ("rubles", "kopecks"),
    [("100000", 10_000_000), ("100000.50", 10_000_050), ("1,01", 101)],
)
def test_rubles_to_kopecks_is_exact(rubles: str, kopecks: int) -> None:
    assert rubles_to_kopecks(rubles) == kopecks


@pytest.mark.parametrize("value", ["0", "-1", "1.001", "nan", "not-money"])
def test_rubles_to_kopecks_rejects_invalid_threshold(value: str) -> None:
    with pytest.raises(ValueError):
        rubles_to_kopecks(value)
