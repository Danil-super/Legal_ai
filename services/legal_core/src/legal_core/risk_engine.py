"""Deterministic, fail-closed risk assessment for a frozen case-fact snapshot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from legal_core.contracts import FactKey, MissingFact, MissingFactSeverity


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """An approved policy snapshot supplied by the policy repository."""

    version: str
    high_demand_threshold_kopecks: int
    guided_v2_explicit_signals_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.version or len(self.version) > 80:
            raise ValueError("risk policy version must be between 1 and 80 characters")
        if self.high_demand_threshold_kopecks < 1:
            raise ValueError("high demand threshold must be positive")
        if type(self.guided_v2_explicit_signals_enabled) is not bool:
            raise ValueError("guided v2 risk capability must be an explicit boolean")
        if self.guided_v2_explicit_signals_enabled:
            _, _, version_number = self.version.rpartition(".v")
            if not version_number.isdigit() or int(version_number) < 3:
                raise ValueError("guided v2 risk capability requires policy version >= 3")
            if self.high_demand_threshold_kopecks != 5_000_000:
                raise ValueError("v3 risk threshold must remain 50,000 RUB")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: RiskLevel
    reason_codes: tuple[str, ...]
    policy_version: str
    fact_snapshot_sha256: str
    external_draft_allowed: bool


def risk_missing_facts(assessment: RiskAssessment) -> list[MissingFact]:
    """Map deterministic blockers to bounded questions, without changing confirmed facts."""
    questions = {
        "HEALTH_CONSEQUENCE_SIGNALS_UNKNOWN": (
            FactKey.HEALTH_CONSEQUENCE_SIGNALS,
            "health_consequence_signals",
        ),
        "INCOMING_COMMUNICATION_UNKNOWN": (
            FactKey.INCOMING_COMMUNICATION,
            "incoming_communication",
        ),
        "HOSPITALIZATION_CONFIRMATION_CONFLICT": (
            FactKey.HEALTH_CONSEQUENCE_SIGNALS,
            "health_consequence_signals",
        ),
        "REGULATOR_OR_COURT_CONFIRMATION_CONFLICT": (
            FactKey.INCOMING_COMMUNICATION,
            "incoming_communication",
        ),
    }
    return (
        [
            MissingFact(
                factKey=questions[reason][0],
                questionId=questions[reason][1],
                reasonCode=reason,
                severity=MissingFactSeverity.CRITICAL,
            )
            for reason in assessment.reason_codes
            if reason in questions
        ]
        if assessment.level is RiskLevel.UNAVAILABLE
        else []
    )


_REQUIRED_SIGNALS = (
    FactKey.HARM_CLAIMED,
    FactKey.LAWYER_CONTACT,
    FactKey.FORMAL_CLAIM,
    FactKey.REGULATOR_OR_COURT,
    FactKey.REGULATOR_THREAT,
)

_GUIDED_HEALTH = frozenset(
    {
        "NO_KNOWN_INFORMATION",
        "COMPLICATION_OR_WORSENING",
        "OTHER_CLINIC",
        "HOSPITALIZATION",
        "OTHER_CONSEQUENCE",
        "UNKNOWN",
    }
)
_GUIDED_INCOMING = frozenset(
    {
        "MESSAGE_OR_REQUEST",
        "COMPLAINT",
        "FORMAL_DOCUMENT",
        "AUTHORITY_OR_COURT_DOCUMENT",
        "OTHER",
        "UNKNOWN",
    }
)


def _guided_health_signals(facts: Mapping[FactKey, object]) -> tuple[str, ...] | None:
    value = facts.get(FactKey.HEALTH_CONSEQUENCE_SIGNALS)
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 10
        or not all(isinstance(item, str) and item in _GUIDED_HEALTH for item in value)
        or len(value) != len(set(value))
        or (len(value) > 1 and any(item in {"UNKNOWN", "NO_KNOWN_INFORMATION"} for item in value))
    ):
        return None
    return tuple(value)


def _guided_enabled(facts: Mapping[FactKey, object], policy: RiskPolicy) -> bool:
    return (
        policy.guided_v2_explicit_signals_enabled
        and facts.get(FactKey.INTAKE_VERSION) == "GUIDED_V2"
    )


def _v3_critical_reasons(facts: Mapping[FactKey, object], policy: RiskPolicy) -> tuple[str, ...]:
    guided = _guided_enabled(facts, policy)
    health = _guided_health_signals(facts) if guided else None
    reasons: list[str] = []
    if (health is not None and "HOSPITALIZATION" in health) or _signal_state(
        facts.get(FactKey.HOSPITALIZATION)
    ) == "YES":
        reasons.append("HOSPITALIZATION_REPORTED")
    if guided and facts.get(FactKey.INCOMING_COMMUNICATION) == "AUTHORITY_OR_COURT_DOCUMENT":
        reasons.append("AUTHORITY_OR_COURT_DOCUMENT_REPORTED")
    elif _signal_state(facts.get(FactKey.REGULATOR_OR_COURT)) == "YES":
        reasons.append("OFFICIAL_REGULATOR_OR_COURT_SIGNAL")
    return tuple(reasons)


def _v3_guided_blocker(facts: Mapping[FactKey, object]) -> str | None:
    health = _guided_health_signals(facts)
    if (
        health is not None
        and "HOSPITALIZATION" in health
        and _signal_state(facts.get(FactKey.HOSPITALIZATION)) == "NO"
    ):
        return "HOSPITALIZATION_CONFIRMATION_CONFLICT"
    incoming = facts.get(FactKey.INCOMING_COMMUNICATION)
    if (
        incoming == "AUTHORITY_OR_COURT_DOCUMENT"
        and _signal_state(facts.get(FactKey.REGULATOR_OR_COURT)) == "NO"
    ):
        return "REGULATOR_OR_COURT_CONFIRMATION_CONFLICT"
    if not isinstance(incoming, str) or incoming not in _GUIDED_INCOMING or incoming == "UNKNOWN":
        return "INCOMING_COMMUNICATION_UNKNOWN"
    if health is None or "HOSPITALIZATION" not in health:
        # The current questionnaire has no explicit negative safety answer.
        # Even NO_KNOWN_INFORMATION must not manufacture a legacy NO.
        return "HEALTH_CONSEQUENCE_SIGNALS_UNKNOWN"
    return None


def fact_snapshot_sha256(facts: Mapping[FactKey, object]) -> str:
    """Hash the canonical typed-fact snapshot used by risk and analysis concurrency checks."""

    payload = {
        key.value: value for key, value in sorted(facts.items(), key=lambda item: item[0].value)
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
        fact_snapshot_sha256=fact_snapshot_sha256(facts),
        external_draft_allowed=level is RiskLevel.LOW,
    )


def evaluate_early_triage(
    facts: Mapping[FactKey, object], *, policy: RiskPolicy
) -> RiskAssessment | None:
    """Route explicit safety signals, never produce a legal conclusion or LOW clearance."""
    if policy.guided_v2_explicit_signals_enabled:
        critical_reasons = _v3_critical_reasons(facts, policy)
        if critical_reasons:
            return _assessment(RiskLevel.CRITICAL, critical_reasons, policy, facts)
    for key, reason in (
        (FactKey.HOSPITALIZATION, "HOSPITALIZATION_REPORTED"),
        (FactKey.REGULATOR_OR_COURT, "OFFICIAL_REGULATOR_OR_COURT_SIGNAL"),
    ):
        if _signal_state(facts.get(key)) == "YES":
            return _assessment(RiskLevel.CRITICAL, (reason,), policy, facts)
    reasons = [
        reason
        for key, reason in (
            (FactKey.LAWYER_CONTACT, "LAWYER_OR_REPRESENTATIVE_CONTACT"),
            (FactKey.FORMAL_CLAIM, "FORMAL_CLAIM_RECEIVED"),
            (FactKey.HARM_CLAIMED, "HARM_REPORTED"),
        )
        if _signal_state(facts.get(key)) == "YES"
    ]
    if _demand_is_at_or_above_threshold(facts, policy.high_demand_threshold_kopecks):
        reasons.append("HIGH_DEMAND_AMOUNT")
    return _assessment(RiskLevel.HIGH, tuple(reasons), policy, facts) if reasons else None


def evaluate_risk(
    facts: Mapping[FactKey, object],
    *,
    policy: RiskPolicy,
    evidence_verified: bool,
) -> RiskAssessment:
    """Assess facts without model inference and fail closed on absent safety prerequisites."""

    if not evidence_verified:
        return _assessment(RiskLevel.UNAVAILABLE, ("EVIDENCE_NOT_VERIFIED",), policy, facts)

    if _guided_enabled(facts, policy):
        blocker = _v3_guided_blocker(facts)
        if blocker:
            return _assessment(RiskLevel.UNAVAILABLE, (blocker,), policy, facts)

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
    if policy.guided_v2_explicit_signals_enabled:
        critical_reasons = _v3_critical_reasons(facts, policy)
        if critical_reasons:
            return _assessment(RiskLevel.CRITICAL, critical_reasons, policy, facts)
    if hospitalization == "YES":
        return _assessment(RiskLevel.CRITICAL, ("HOSPITALIZATION_REPORTED",), policy, facts)
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
