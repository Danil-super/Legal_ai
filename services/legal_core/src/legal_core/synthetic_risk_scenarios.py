"""Hash-stable, fully synthetic regression scenarios for the risk engine.

This pack is deliberately not a practical-case corpus, a legal source, or model-training data.
It contains only typed, non-identifying facts and expected deterministic risk outcomes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from legal_core.contracts import FactKey
from legal_core.risk_engine import RiskLevel, RiskPolicy, evaluate_risk

_FIXTURE_ROOT = Path(__file__).parents[2] / "corpus" / "synthetic_risk_scenarios"
_MANIFEST = _FIXTURE_ROOT / "risk_scenarios.v1.json"
_SCENARIO_ID = re.compile(r"^risk-[a-z0-9-]{3,80}$")
_EXPECTED_KEYS = {"level", "reason_codes", "external_draft_allowed"}
_SCENARIO_KEYS = {"id", "priority", "facts", "evidence_verified", "expected"}
_ALLOWED_FACT_KEYS = {
    FactKey.HARM_CLAIMED,
    FactKey.HOSPITALIZATION,
    FactKey.LAWYER_CONTACT,
    FactKey.FORMAL_CLAIM,
    FactKey.REGULATOR_OR_COURT,
    FactKey.REGULATOR_THREAT,
    FactKey.PATIENT_DEMAND,
    FactKey.DEMAND_AMOUNT,
    FactKey.CLINIC_DOCUMENTS,
}
_SIGNAL_FACT_KEYS = {
    FactKey.HARM_CLAIMED,
    FactKey.HOSPITALIZATION,
    FactKey.LAWYER_CONTACT,
    FactKey.FORMAL_CLAIM,
    FactKey.REGULATOR_OR_COURT,
    FactKey.REGULATOR_THREAT,
}
_ALLOWED_DEMANDS = {
    "AESTHETIC_DISSATISFACTION",
    "COMPENSATION_DEMAND",
    "NEGATIVE_REVIEW_PRESSURE",
    "REFUND_DEMAND",
    "REWORK_DEMAND",
}


@dataclass(frozen=True, slots=True)
class SyntheticRiskScenario:
    scenario_id: str
    priority: str
    facts: dict[FactKey, object]
    evidence_verified: bool
    expected_level: RiskLevel
    expected_reason_codes: tuple[str, ...]
    expected_external_draft_allowed: bool


@dataclass(frozen=True, slots=True)
class SyntheticRiskScenarioPack:
    policy: RiskPolicy
    scenarios: tuple[SyntheticRiskScenario, ...]


def _require_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"synthetic risk {label} must be an object")
    return value


def _parse_facts(raw: object) -> dict[FactKey, object]:
    payload = _require_mapping(raw, label="facts")
    result: dict[FactKey, object] = {}
    for key, value in payload.items():
        try:
            fact_key = FactKey(key)
        except ValueError as exc:
            raise ValueError("synthetic risk scenario has an unsupported fact key") from exc
        if fact_key not in _ALLOWED_FACT_KEYS:
            raise ValueError("synthetic risk scenario fact is outside risk-engine scope")
        if fact_key in _SIGNAL_FACT_KEYS:
            if value not in {"YES", "NO", "UNKNOWN"}:
                raise ValueError("synthetic risk signal must be YES, NO or UNKNOWN")
        elif fact_key is FactKey.PATIENT_DEMAND:
            if not isinstance(value, list) or any(item not in _ALLOWED_DEMANDS for item in value):
                raise ValueError("synthetic risk demands must use the bounded vocabulary")
        elif fact_key is FactKey.DEMAND_AMOUNT:
            if (
                not isinstance(value, dict)
                or set(value) != {"amountKopecks", "currency"}
                or isinstance(value.get("amountKopecks"), bool)
                or not isinstance(value.get("amountKopecks"), int)
                or value.get("amountKopecks", 0) < 1
                or value.get("currency") != "RUB"
            ):
                raise ValueError("synthetic risk demand amount has an invalid shape")
        elif fact_key is FactKey.CLINIC_DOCUMENTS and (
            not isinstance(value, dict)
            or not value
            or any(
                not isinstance(document_type, str)
                or status not in {"AVAILABLE", "MISSING"}
                for document_type, status in value.items()
            )
        ):
            raise ValueError("synthetic risk document inventory is invalid")
        result[fact_key] = value
    return result


def load_synthetic_risk_scenario_pack() -> SyntheticRiskScenarioPack:
    """Load the immutable local-only risk regression pack without external I/O."""

    payload = _require_mapping(json.loads(_MANIFEST.read_text(encoding="utf-8")), label="manifest")
    if payload.get("schema_version") != "synthetic-risk-scenarios.v1":
        raise ValueError("unsupported synthetic risk scenario manifest")
    if payload.get("purpose") != "DEVELOPMENT_AND_REGRESSION_ONLY":
        raise ValueError("synthetic risk scenario purpose changed")
    if payload.get("authority") != "NOT_A_LEGAL_SOURCE":
        raise ValueError("synthetic risk scenarios cannot become legal authority")
    if payload.get("source_policy") != "AUTHORED_SYNTHETIC_NO_EXTERNAL_CASE_TEXT":
        raise ValueError("synthetic risk scenario source policy changed")

    policy_payload = _require_mapping(payload.get("policy"), label="policy")
    if set(policy_payload) != {"version", "high_demand_threshold_kopecks"}:
        raise ValueError("synthetic risk policy shape is unsupported")
    version = policy_payload["version"]
    threshold = policy_payload["high_demand_threshold_kopecks"]
    if (
        not isinstance(version, str)
        or isinstance(threshold, bool)
        or not isinstance(threshold, int)
    ):
        raise ValueError("synthetic risk policy is invalid")
    policy = RiskPolicy(version=version, high_demand_threshold_kopecks=threshold)

    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list) or not 20 <= len(raw_scenarios) <= 100:
        raise ValueError("synthetic risk scenario pack must contain 20-100 scenarios")

    scenarios: list[SyntheticRiskScenario] = []
    seen_ids: set[str] = set()
    for raw_scenario in raw_scenarios:
        scenario = _require_mapping(raw_scenario, label="scenario")
        if set(scenario) != _SCENARIO_KEYS:
            raise ValueError("synthetic risk scenario has an unsupported shape")
        scenario_id = scenario["id"]
        priority = scenario["priority"]
        evidence_verified = scenario["evidence_verified"]
        if (
            not isinstance(scenario_id, str)
            or _SCENARIO_ID.fullmatch(scenario_id) is None
            or scenario_id in seen_ids
        ):
            raise ValueError("synthetic risk scenario id must be unique and bounded")
        if priority not in {"P0", "P1"} or type(evidence_verified) is not bool:
            raise ValueError("synthetic risk scenario metadata is invalid")
        seen_ids.add(scenario_id)

        expected = _require_mapping(scenario["expected"], label="expected outcome")
        if set(expected) != _EXPECTED_KEYS:
            raise ValueError("synthetic risk expected outcome has an unsupported shape")
        level = expected["level"]
        reason_codes = expected["reason_codes"]
        external_draft_allowed = expected["external_draft_allowed"]
        if not isinstance(level, str):
            raise ValueError("synthetic risk expected level is invalid")
        try:
            expected_level = RiskLevel(level)
        except ValueError as exc:
            raise ValueError("synthetic risk expected level is invalid") from exc
        if (
            not isinstance(reason_codes, list)
            or not reason_codes
            or any(not isinstance(code, str) or not code.isupper() for code in reason_codes)
            or type(external_draft_allowed) is not bool
            or external_draft_allowed is not (expected_level is RiskLevel.LOW)
        ):
            raise ValueError("synthetic risk expected outcome is inconsistent")

        scenarios.append(
            SyntheticRiskScenario(
                scenario_id=scenario_id,
                priority=priority,
                facts=_parse_facts(scenario["facts"]),
                evidence_verified=evidence_verified,
                expected_level=expected_level,
                expected_reason_codes=tuple(reason_codes),
                expected_external_draft_allowed=external_draft_allowed,
            )
        )

    return SyntheticRiskScenarioPack(policy=policy, scenarios=tuple(scenarios))


def assert_p0_synthetic_risk_regressions(policy: RiskPolicy) -> None:
    """Fail policy promotion if the approved P0 synthetic outcomes would drift.

    Scenario identifiers are bounded non-identifying fixture IDs, so the failure is safe to
    surface in the approval CLI/audit path. P1 scenarios remain covered by the ordinary CI suite.
    """

    pack = load_synthetic_risk_scenario_pack()
    failures: list[str] = []
    for scenario in pack.scenarios:
        if scenario.priority != "P0":
            continue
        actual = evaluate_risk(
            scenario.facts,
            policy=policy,
            evidence_verified=scenario.evidence_verified,
        )
        if (
            actual.level is not scenario.expected_level
            or actual.reason_codes != scenario.expected_reason_codes
            or actual.external_draft_allowed is not scenario.expected_external_draft_allowed
        ):
            failures.append(scenario.scenario_id)

    if failures:
        raise ValueError(
            "synthetic P0 risk regressions failed: " + ", ".join(sorted(failures))
        )
