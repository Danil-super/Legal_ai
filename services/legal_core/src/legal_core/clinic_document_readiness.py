"""Deterministic, non-blocking expectations for clinic-owned case documents.

These expectations are product workflow hints, not legal requirements. A NOT_AVAILABLE status means
that no applicable approved clinic template/policy was available to Legal Core for the case date; it
does not mean that the clinic is legally required to possess a document with that exact taxonomy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from legal_core.clinic_document_retrieval import (
    ApprovedClinicDocumentFragment,
    AvailableClinicDocument,
)
from legal_core.contracts import FactKey


class ClinicDocumentExpectationImportance(StrEnum):
    CORE = "CORE"
    SCENARIO = "SCENARIO"
    SUPPORTING = "SUPPORTING"


class ClinicDocumentReadinessStatus(StrEnum):
    RETRIEVED = "RETRIEVED"
    AVAILABLE_NOT_RETRIEVED = "AVAILABLE_NOT_RETRIEVED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True, slots=True)
class ClinicDocumentExpectation:
    expectation_code: str
    importance: ClinicDocumentExpectationImportance
    accepted_document_types: tuple[str, ...]
    reason_code: str


@dataclass(frozen=True, slots=True)
class ClinicDocumentReadiness:
    expectation_code: str
    importance: ClinicDocumentExpectationImportance
    accepted_document_types: tuple[str, ...]
    reason_code: str
    status: ClinicDocumentReadinessStatus
    matched_document_keys: tuple[str, ...]


def _tokens(value: object) -> set[str]:
    if isinstance(value, str):
        return {value.upper()}
    if isinstance(value, dict):
        raw_values = value.get("values")
        if isinstance(raw_values, (list, tuple, set)):
            return {str(item).upper() for item in raw_values}
        raw_value = value.get("value")
        if isinstance(raw_value, str):
            return {raw_value.upper()}
    if isinstance(value, (list, tuple, set)):
        return {str(item).upper() for item in value}
    return set()


def _is_yes(value: object) -> bool:
    return value is True or value == "YES"


def _service_text(facts: Mapping[FactKey, object]) -> str:
    raw = facts.get(FactKey.SERVICE_TYPE)
    return raw.casefold() if isinstance(raw, str) else ""


def _incident_tokens(facts: Mapping[FactKey, object]) -> set[str]:
    values = _tokens(facts.get(FactKey.INCIDENT_TYPES))
    values.update(_tokens(facts.get(FactKey.PRIMARY_INCIDENT_TYPE)))
    return values


def _has_incident_marker(facts: Mapping[FactKey, object], markers: Sequence[str]) -> bool:
    incidents = _incident_tokens(facts)
    return any(marker in token for token in incidents for marker in markers)


def _has_demand_marker(facts: Mapping[FactKey, object], markers: Sequence[str]) -> bool:
    demands = _tokens(facts.get(FactKey.PATIENT_DEMAND))
    return any(marker in token for token in demands for marker in markers)


def _expectation(
    code: str,
    importance: ClinicDocumentExpectationImportance,
    types: tuple[str, ...],
    reason: str,
) -> ClinicDocumentExpectation:
    return ClinicDocumentExpectation(
        expectation_code=code,
        importance=importance,
        accepted_document_types=types,
        reason_code=reason,
    )


def plan_clinic_document_expectations(
    facts: Mapping[FactKey, object],
) -> tuple[ClinicDocumentExpectation, ...]:
    """Return a stable, bounded document checklist for internal case preparation."""

    planned: list[ClinicDocumentExpectation] = [
        _expectation(
            "CONTRACT",
            ClinicDocumentExpectationImportance.CORE,
            ("CONTRACT", "CONTRACT_MINOR", "CONTRACT_THREE_PARTY", "CONTRACT_LEGAL_ENTITY"),
            "CORE_SERVICE_CONTRACT_CONTEXT",
        ),
        _expectation(
            "GENERAL_CONSENT",
            ClinicDocumentExpectationImportance.CORE,
            ("INFORMED_CONSENT_GENERAL", "INFORMED_CONSENT_SPECIALTY"),
            "CORE_INFORMED_CONSENT_CONTEXT",
        ),
    ]

    service = _service_text(facts)
    implant_case = "имплант" in service or _has_incident_marker(facts, ("IMPLANT",))
    warranty_case = (
        any(marker in service for marker in ("корон", "винир", "протез", "реставрац", "имплант"))
        or _has_incident_marker(
            facts,
            ("CROWN", "VENEER", "RESTORATION", "IMPLANT", "PROSTH"),
        )
    )
    records_case = _has_incident_marker(facts, ("RECORD", "DOCUMENT")) or _has_demand_marker(
        facts,
        ("RECORD", "DOCUMENT"),
    )
    formal_claim_case = _is_yes(facts.get(FactKey.FORMAL_CLAIM)) or _has_incident_marker(
        facts,
        ("FORMAL_CLAIM",),
    )
    regulator_case = _is_yes(facts.get(FactKey.REGULATOR_THREAT)) or _is_yes(
        facts.get(FactKey.REGULATOR_OR_COURT)
    )

    if warranty_case:
        planned.append(
            _expectation(
                "WARRANTY_POLICY",
                ClinicDocumentExpectationImportance.SCENARIO,
                ("WARRANTY_POLICY",),
                "WARRANTY_SENSITIVE_SERVICE_OR_INCIDENT",
            )
        )
    if implant_case:
        planned.extend(
            [
                _expectation(
                    "IMPLANT_CONSENT",
                    ClinicDocumentExpectationImportance.SCENARIO,
                    (
                        "INFORMED_CONSENT_IMPLANT",
                        "INFORMED_CONSENT_SURGERY",
                        "INFORMED_CONSENT_SPECIALTY",
                    ),
                    "IMPLANT_CASE_SPECIALTY_CONSENT",
                ),
                _expectation(
                    "POST_IMPLANT_MEMO",
                    ClinicDocumentExpectationImportance.SUPPORTING,
                    ("PATIENT_MEMO_POST_IMPLANT", "PATIENT_MEMO"),
                    "IMPLANT_CASE_POST_TREATMENT_INSTRUCTIONS",
                ),
            ]
        )
    if records_case:
        planned.append(
            _expectation(
                "MEDICAL_RECORD_ACCESS",
                ClinicDocumentExpectationImportance.SCENARIO,
                ("MEDICAL_RECORD_ACCESS_POLICY",),
                "MEDICAL_RECORD_REQUEST_CONTEXT",
            )
        )
    if formal_claim_case:
        planned.append(
            _expectation(
                "CLAIM_WORKFLOW",
                ClinicDocumentExpectationImportance.SUPPORTING,
                ("CLAIM_POLICY",),
                "FORMAL_CLAIM_INTERNAL_WORKFLOW",
            )
        )
    if regulator_case:
        planned.append(
            _expectation(
                "PATIENT_RULES",
                ClinicDocumentExpectationImportance.SUPPORTING,
                ("PATIENT_RULES",),
                "REGULATOR_OR_CONFLICT_COMMUNICATION_CONTEXT",
            )
        )

    unique: dict[str, ClinicDocumentExpectation] = {}
    for item in planned:
        unique.setdefault(item.expectation_code, item)
    return tuple(unique.values())


def assess_clinic_document_readiness(
    expectations: Sequence[ClinicDocumentExpectation],
    *,
    available_documents: Sequence[AvailableClinicDocument],
    retrieved_fragments: Sequence[ApprovedClinicDocumentFragment],
) -> tuple[ClinicDocumentReadiness, ...]:
    """Distinguish missing approved context from a bounded-retrieval miss."""

    available_by_type: dict[str, set[str]] = {}
    for document in available_documents:
        available_by_type.setdefault(document.document_type, set()).add(document.document_key)
    retrieved_by_type: dict[str, set[str]] = {}
    for fragment in retrieved_fragments:
        retrieved_by_type.setdefault(fragment.document_type, set()).add(fragment.document_key)

    result: list[ClinicDocumentReadiness] = []
    for expectation in expectations:
        retrieved_keys = sorted(
            {
                key
                for document_type in expectation.accepted_document_types
                for key in retrieved_by_type.get(document_type, set())
            }
        )
        available_keys = sorted(
            {
                key
                for document_type in expectation.accepted_document_types
                for key in available_by_type.get(document_type, set())
            }
        )
        if retrieved_keys:
            status = ClinicDocumentReadinessStatus.RETRIEVED
            matched = tuple(retrieved_keys)
        elif available_keys:
            status = ClinicDocumentReadinessStatus.AVAILABLE_NOT_RETRIEVED
            matched = tuple(available_keys)
        else:
            status = ClinicDocumentReadinessStatus.NOT_AVAILABLE
            matched = ()
        result.append(
            ClinicDocumentReadiness(
                expectation_code=expectation.expectation_code,
                importance=expectation.importance,
                accepted_document_types=expectation.accepted_document_types,
                reason_code=expectation.reason_code,
                status=status,
                matched_document_keys=matched,
            )
        )
    return tuple(result)
