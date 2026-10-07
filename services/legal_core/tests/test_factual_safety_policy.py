"""Only an independently reviewed, hash-covered candidate reads factual screening."""

import pytest
from pydantic import ValidationError

from legal_core.risk_policy_approval import policy_content_sha256, policy_payload
from test_risk_policy_approval import _v3_approval
from test_risk_policy_v3 import load_policy, v3_payload


def test_candidate_factual_capability_is_opt_in_and_hash_covered() -> None:
    original = policy_payload(_v3_approval())
    extended = policy_payload(
        _v3_approval(
            factual_safety_intake_enabled=True,
            factual_safety_intake_reviewed=True,
        )
    )
    assert extended == original | {"factualSafetyIntakeVersion": "factual-safety-intake.v1"}
    assert policy_content_sha256(original) != policy_content_sha256(extended)
    assert load_policy(extended).domain.factual_safety_intake_enabled is True
    assert load_policy(original).domain.factual_safety_intake_enabled is False


@pytest.mark.parametrize(
    "values",
    [
        {"factual_safety_intake_enabled": True},
        {"factual_safety_intake_reviewed": True},
        {"factual_safety_intake_enabled": "true", "factual_safety_intake_reviewed": True},
    ],
)
def test_factual_capability_requires_its_separate_explicit_review(values) -> None:
    with pytest.raises(ValidationError):
        _v3_approval(**values)


@pytest.mark.parametrize("version", [None, True, "factual-safety-intake.v2", ""])
def test_repository_rejects_unknown_factual_capability(version) -> None:
    with pytest.raises(ValueError):
        load_policy(v3_payload() | {"factualSafetyIntakeVersion": version})


def test_historical_policy_cannot_acquire_factual_capability() -> None:
    with pytest.raises(ValueError):
        load_policy(
            {
                "schemaVersion": "risk-policy.v2",
                "highDemandThresholdKopecks": 5_000_000,
                "earlyTriageEnabled": True,
                "factualSafetyIntakeVersion": "factual-safety-intake.v1",
            },
            version=2,
        )


def test_factual_opt_in_cannot_reuse_the_original_policy_hash() -> None:
    original = v3_payload()
    extended = original | {"factualSafetyIntakeVersion": "factual-safety-intake.v1"}
    with pytest.raises(ValueError, match="content hash"):
        load_policy(extended, digest=policy_content_sha256(original))
