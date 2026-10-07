"""Authored typed facts; no patient material or external legal text."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from legal_core.contracts import FactKey
from legal_core.risk_engine import RiskLevel, RiskPolicy, evaluate_early_triage, evaluate_risk
from legal_core.risk_policy_repository import ApprovedRiskPolicyRepository


def v3_policy() -> RiskPolicy:
    return RiskPolicy(
        version="dental-risk.v3",
        high_demand_threshold_kopecks=5_000_000,
        guided_v2_explicit_signals_enabled=True,
    )


def guided_facts(**overrides: object) -> dict[FactKey, object]:
    return {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.INCOMING_COMMUNICATION: "COMPLAINT",
        FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["NO_KNOWN_INFORMATION"],
        **{FactKey(key): value for key, value in overrides.items()},
    }


@pytest.mark.parametrize(
    ("overrides", "reasons"),
    [
        ({"HEALTH_CONSEQUENCE_SIGNALS": ["HOSPITALIZATION"]}, ("HOSPITALIZATION_REPORTED",)),
        (
            {"INCOMING_COMMUNICATION": "AUTHORITY_OR_COURT_DOCUMENT"},
            ("AUTHORITY_OR_COURT_DOCUMENT_REPORTED",),
        ),
        (
            {
                "HEALTH_CONSEQUENCE_SIGNALS": ["HOSPITALIZATION"],
                "INCOMING_COMMUNICATION": "AUTHORITY_OR_COURT_DOCUMENT",
            },
            ("HOSPITALIZATION_REPORTED", "AUTHORITY_OR_COURT_DOCUMENT_REPORTED"),
        ),
        (
            {"HEALTH_CONSEQUENCE_SIGNALS": ["HOSPITALIZATION"], "REGULATOR_OR_COURT": "YES"},
            ("HOSPITALIZATION_REPORTED", "OFFICIAL_REGULATOR_OR_COURT_SIGNAL"),
        ),
    ],
)
def test_v3_routes_explicit_guided_signals_before_evidence(
    overrides: dict[str, object], reasons: tuple[str, ...]
) -> None:
    facts = guided_facts(**overrides)
    result = evaluate_early_triage(facts, policy=v3_policy())
    assert result is not None
    assert result.level is RiskLevel.CRITICAL
    assert result.reason_codes == reasons
    assert result.external_draft_allowed is False
    full = evaluate_risk(facts, policy=v3_policy(), evidence_verified=False)
    assert full.level is RiskLevel.UNAVAILABLE
    assert full.reason_codes == ("EVIDENCE_NOT_VERIFIED",)


def test_v3_deduplicates_same_critical_dimension_and_critical_beats_high() -> None:
    facts = guided_facts(
        HEALTH_CONSEQUENCE_SIGNALS=["HOSPITALIZATION"],
        INCOMING_COMMUNICATION="AUTHORITY_OR_COURT_DOCUMENT",
        HOSPITALIZATION="YES",
        REGULATOR_OR_COURT="YES",
        DEMAND_AMOUNT={"amountKopecks": 5_000_000, "currency": "RUB"},
    )
    result = evaluate_early_triage(facts, policy=v3_policy())
    assert result is not None
    assert result.reason_codes == (
        "HOSPITALIZATION_REPORTED",
        "AUTHORITY_OR_COURT_DOCUMENT_REPORTED",
    )


@pytest.mark.parametrize("version", ["dental-risk.v1", "dental-risk.v2"])
def test_historical_policy_never_reads_guided_signals(version: str) -> None:
    facts = guided_facts(
        HEALTH_CONSEQUENCE_SIGNALS=["HOSPITALIZATION"],
        INCOMING_COMMUNICATION="AUTHORITY_OR_COURT_DOCUMENT",
    )
    policy = RiskPolicy(version=version, high_demand_threshold_kopecks=5_000_000)
    assert evaluate_early_triage(facts, policy=policy) is None
    result = evaluate_risk(facts, policy=policy, evidence_verified=True)
    assert result.reason_codes == ("HARM_CLAIMED_UNKNOWN",)


@pytest.mark.parametrize(
    "health",
    [
        ["UNKNOWN"],
        ["NO_KNOWN_INFORMATION"],
        ["OTHER_CLINIC"],
        ["COMPLICATION_OR_WORSENING"],
        ["OTHER_CONSEQUENCE"],
        None,
        "HOSPITALIZATION",
        ["HOSPITALIZATION", "UNKNOWN"],
        ["hospitalization"],
        ["HOSPITALIZATION", "HOSPITALIZATION"],
    ],
)
def test_v3_does_not_clear_or_infer_health_from_unknown_or_invalid_facts(health: object) -> None:
    facts = guided_facts(HEALTH_CONSEQUENCE_SIGNALS=health)
    assert evaluate_early_triage(facts, policy=v3_policy()) is None
    result = evaluate_risk(facts, policy=v3_policy(), evidence_verified=True)
    assert result.level is RiskLevel.UNAVAILABLE
    assert result.reason_codes == ("HEALTH_CONSEQUENCE_SIGNALS_UNKNOWN",)


@pytest.mark.parametrize("incoming", ["UNKNOWN", "authority_or_court_document", True, None])
def test_v3_unknown_communication_requests_the_exact_fact(incoming: object) -> None:
    facts = guided_facts(
        INCOMING_COMMUNICATION=incoming, HEALTH_CONSEQUENCE_SIGNALS=["HOSPITALIZATION"]
    )
    result = evaluate_risk(facts, policy=v3_policy(), evidence_verified=True)
    assert result.level is RiskLevel.UNAVAILABLE
    assert result.reason_codes == ("INCOMING_COMMUNICATION_UNKNOWN",)


def test_generic_document_and_prose_never_create_a_risk_signal() -> None:
    facts = guided_facts(
        INCOMING_COMMUNICATION="FORMAL_DOCUMENT",
        EVENT_SUMMARY="Synthetic text mentions hospitalization, court and a monetary request.",
        INCOMING_SOURCE_STATUS="FILE_ATTACHED",
    )
    assert evaluate_early_triage(facts, policy=v3_policy()) is None


def test_guided_positive_and_legacy_negative_keep_routing_but_block_final_assessment() -> None:
    facts = guided_facts(HEALTH_CONSEQUENCE_SIGNALS=["HOSPITALIZATION"], HOSPITALIZATION="NO")
    early = evaluate_early_triage(facts, policy=v3_policy())
    assert early is not None and early.level is RiskLevel.CRITICAL
    full = evaluate_risk(facts, policy=v3_policy(), evidence_verified=True)
    assert full.level is RiskLevel.UNAVAILABLE
    assert full.reason_codes == ("HOSPITALIZATION_CONFIRMATION_CONFLICT",)


@pytest.mark.parametrize(("amount", "level"), [(4_999_999, None), (5_000_000, RiskLevel.HIGH)])
def test_v3_early_demand_boundary_is_exact_and_inclusive(amount: int, level: RiskLevel | None):
    facts = {FactKey.DEMAND_AMOUNT: {"amountKopecks": amount, "currency": "RUB"}}
    result = evaluate_early_triage(facts, policy=v3_policy())
    assert (None if result is None else result.level) == level


def v3_payload() -> dict[str, object]:
    return {
        "schemaVersion": "risk-policy.v3",
        "highDemandThresholdKopecks": 5_000_000,
        "earlyTriageEnabled": True,
        "guidedV2ExplicitSignalsEnabled": True,
    }


def load_policy(payload: dict[str, object], *, version: int = 3, digest: str | None = None):
    class Session:
        async def scalar(self, query):
            return SimpleNamespace(
                id=uuid4(),
                policy_key="dental-risk",
                version=version,
                policy_json=payload,
                content_sha256=digest
                or hashlib.sha256(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            )

    return asyncio.run(ApprovedRiskPolicyRepository(Session()).get())


def test_repository_loads_only_explicit_canonical_v3_capability() -> None:
    policy = load_policy(v3_payload())
    assert policy.early_triage_enabled is True
    assert policy.domain.guided_v2_explicit_signals_enabled is True
    assert policy.domain.version == "dental-risk.v3"


@pytest.mark.parametrize(
    "mutation",
    [
        {"guidedV2ExplicitSignalsEnabled": False},
        {"earlyTriageEnabled": False},
        {"highDemandThresholdKopecks": 4_999_999},
        {"highDemandThresholdKopecks": True},
        {"unexpected": True},
        {"schemaVersion": "risk-policy.v4"},
    ],
)
def test_repository_rejects_unsupported_v3_content(mutation: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        load_policy(v3_payload() | mutation)


def test_repository_rejects_v3_schema_on_a_historical_policy_version_or_tampered_hash() -> None:
    with pytest.raises(ValueError):
        load_policy(v3_payload(), version=2)
    with pytest.raises(ValueError):
        load_policy(v3_payload(), digest="0" * 64)


def test_v3_missing_fact_reason_selects_the_existing_question_contract() -> None:
    from legal_core.risk_engine import risk_missing_facts

    result = evaluate_risk(guided_facts(), policy=v3_policy(), evidence_verified=True)
    missing = risk_missing_facts(result)
    assert [(item.fact_key, item.question_id, item.reason_code) for item in missing] == [
        (
            FactKey.HEALTH_CONSEQUENCE_SIGNALS,
            "health_consequence_signals",
            "HEALTH_CONSEQUENCE_SIGNALS_UNKNOWN",
        )
    ]
    conflict = evaluate_risk(
        guided_facts(HEALTH_CONSEQUENCE_SIGNALS=["HOSPITALIZATION"], HOSPITALIZATION="NO"),
        policy=v3_policy(),
        evidence_verified=True,
    )
    assert risk_missing_facts(conflict)[0].question_id == "health_consequence_signals"


@pytest.mark.parametrize("invalid", ["INVALID", 1, {}, []])
def test_v3_invalid_legacy_safety_state_cannot_clear_a_case(invalid: object) -> None:
    facts = {
        key: "NO"
        for key in (
            FactKey.HARM_CLAIMED,
            FactKey.HOSPITALIZATION,
            FactKey.LAWYER_CONTACT,
            FactKey.FORMAL_CLAIM,
            FactKey.REGULATOR_OR_COURT,
            FactKey.REGULATOR_THREAT,
        )
    } | {FactKey.HARM_CLAIMED: invalid}
    result = evaluate_risk(facts, policy=v3_policy(), evidence_verified=True)
    assert result.level is RiskLevel.UNAVAILABLE
    assert result.reason_codes == ("HARM_CLAIMED_UNKNOWN",)
