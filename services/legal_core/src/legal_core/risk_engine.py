"""Deterministic, fail-closed risk assessment for a frozen case-fact snapshot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from legal_core.contracts import FactKey


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """An approved policy snapshot supplied by the policy repository in a later slice."""

    version: str
    high_demand_threshold_kopecks: int

    def __post_init__(self) -> None:
        if not self.version or len(self.version) > 80:
            raise ValueError("risk policy version must be between 1 and 80 characters")
        if self.high_demand_threshold_kopecks < 1:
            raise ValueError("high demand threshold must be positive")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: RiskLevel
    reason_codes: tuple[str, ...]
    policy_version: str
    fact_snapshot_sha256: str
    external_draft_allowed: bool


_REQUIRED_SIGNALS = (
    FactKey.HARM_CLAIMED,
    FactKey.LAWYER_CONTACT,
    FactKey.FORMAL_CLAIM,
    FactKey.REGULATOR_OR_COURT,
    FactKey.REGULATOR_THREAT,
)


def _canonical_fact_snapshot(facts: Mapping[FactKey, object]) -> str:
    payload = {
        key.value: value
        for key, value in sorted(facts.items(), key=lambda item: item[0].value)
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _signal_state(value: object) -> str | None:
    if value is True or value == "YES":
        return "YES"
    if value is False or value == "NO":
        return "NO"
    if value == "UNKNOWN" or value is None:
        return "UNKNOWN"
    return None


def _unknown_required_signal(facts: Mapping[FactKey, object]) -> FactKey | None:
    for fact_key in _REQUIRED_SIGNALS:
        if _signal_state(facts.get(fact_key)) == "UNKNOWN":
            return fact_key
    harm = _signal_state(facts.get(FactKey.HARM_CLAIMED))
    if harm == "YES" and _signal_state(facts.get(FactKey.HOSPITALIZATION)) == "UNKNOWN":
        return FactKey.HOSPITALIZATION
    return None


def _demand_is_at_or_above_threshold(
    facts: Mapping[FactKey, object], threshold_kopecks: int
) -> bool:
    value = facts.get(FactKey.DEMAND_AMOUNT)
    if not isinstance(value, dict) or set(value) != {"amountKopecks", "currency"}:
        return False
    amount = value["amountKopecks"]
    return (
        value["currency"] == "RUB"
        and isinstance(amount, int)
        and not isinstance(amount, bool)
        and amount >= threshold_kopecks
    )


def _has_missing_relevant_document(facts: Mapping[FactKey, object]) -> bool:
    inventory = facts.get(FactKey.CLINIC_DOCUMENTS)
    return isinstance(inventory, dict) and any(value == "MISSING" for value in inventory.values())


def _assessment(
    level: RiskLevel,
    reasons: tuple[str, ...],
    policy: RiskPolicy,
    facts: Mapping[FactKey, object],
) -> RiskAssessment:
    return RiskAssessment(
        level=level,
        reason_codes=reasons,
        policy_version=policy.version,
        fact_snapshot_sha256=_canonical_fact_snapshot(facts),
        external_draft_allowed=level is RiskLevel.LOW,
    )


def evaluate_risk(
    facts: Mapping[FactKey, object],
    *,
    policy: RiskPolicy,
    evidence_verified: bool,
) -> RiskAssessment:
    """Assess facts without model inference and fail closed on absent safety prerequisites."""

    if not evidence_verified:
        return _assessment(
            RiskLevel.UNAVAILABLE, ("EVIDENCE_NOT_VERIFIED",), policy, facts
        )

    unknown_signal = _unknown_required_signal(facts)
    if unknown_signal is not None:
        return _assessment(
            RiskLevel.UNAVAILABLE,
            (f"{unknown_signal.value}_UNKNOWN",),
            policy,
            facts,
        )

    hospitalization = _signal_state(facts.get(FactKey.HOSPITALIZATION))
    regulator_or_court = _signal_state(facts.get(FactKey.REGULATOR_OR_COURT))
    if hospitalization == "YES":
        return _assessment(
            RiskLevel.CRITICAL, ("HOSPITALIZATION_REPORTED",), policy, facts
        )
    if regulator_or_court == "YES":
        return _assessment(
            RiskLevel.CRITICAL, ("OFFICIAL_REGULATOR_OR_COURT_SIGNAL",), policy, facts
        )

    high_reasons: list[str] = []
    if _signal_state(facts.get(FactKey.LAWYER_CONTACT)) == "YES":
        high_reasons.append("LAWYER_OR_REPRESENTATIVE_CONTACT")
    if _signal_state(facts.get(FactKey.FORMAL_CLAIM)) == "YES":
        high_reasons.append("FORMAL_CLAIM_RECEIVED")
    if _signal_state(facts.get(FactKey.HARM_CLAIMED)) == "YES":
        high_reasons.append("HARM_REPORTED")
    if _demand_is_at_or_above_threshold(facts, policy.high_demand_threshold_kopecks):
        high_reasons.append("HIGH_DEMAND_AMOUNT")
    if high_reasons:
        return _assessment(RiskLevel.HIGH, tuple(high_reasons), policy, facts)

    medium_reasons: list[str] = []
    demands = facts.get(FactKey.PATIENT_DEMAND)
    if isinstance(demands, (list, tuple, set)) and any(
        value in {"REFUND_DEMAND", "COMPENSATION_DEMAND", "NEGATIVE_REVIEW_PRESSURE"}
        for value in demands
    ):
        medium_reasons.append("PATIENT_DEMAND_REQUIRES_REVIEW")
    if _signal_state(facts.get(FactKey.REGULATOR_THREAT)) == "YES":
        medium_reasons.append("REGULATOR_THREAT_REPORTED")
    if _has_missing_relevant_document(facts):
        medium_reasons.append("RELEVANT_DOCUMENT_MISSING")
    if medium_reasons:
        return _assessment(RiskLevel.MEDIUM, tuple(medium_reasons), policy, facts)

    return _assessment(RiskLevel.LOW, ("NO_ESCALATION_TRIGGER",), policy, facts)
