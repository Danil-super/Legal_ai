"""Build the only case projection allowed to cross into Hermes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from agent_orchestrator.contracts import CaseProjection, EvidenceItem
from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment
from legal_core.pseudonymization import contains_obvious_direct_identifier, pseudonymize_text


def _pseudonymize_value(
    value: object,
    *,
    known_identifiers: dict[str, str],
) -> Any:
    if isinstance(value, str):
        redacted = pseudonymize_text(value, known_identifiers=known_identifiers).text
        if contains_obvious_direct_identifier(redacted):
            raise ValueError("case fact still contains an obvious direct identifier after redaction")
        return redacted
    if isinstance(value, dict):
        return {
            str(key): _pseudonymize_value(item, known_identifiers=known_identifiers)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_pseudonymize_value(item, known_identifiers=known_identifiers) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise TypeError(f"unsupported fact value type for agent projection: {type(value).__name__}")


def build_case_projection(
    *,
    case_id: UUID,
    as_of_date: date,
    facts: Mapping[FactKey, object],
    evidence: Sequence[ApprovedLegalFragment],
    known_identifiers: dict[str, str] | None = None,
) -> CaseProjection:
    if not evidence:
        raise ValueError("agent projection requires approved legal evidence")
    identifiers = known_identifiers or {}
    projected_facts = {
        key.value: _pseudonymize_value(value, known_identifiers=identifiers)
        for key, value in sorted(facts.items(), key=lambda item: item[0].value)
    }
    projected_evidence = [
        EvidenceItem(
            fragmentId=fragment.fragment_id,
            documentTitle=fragment.document_title,
            officialNumber=fragment.official_number,
            structuralPath=fragment.structural_path,
            text=fragment.fragment_text,
            effectiveFrom=fragment.effective_from,
            effectiveTo=fragment.effective_to,
            sourceUrl=fragment.source_url,
        )
        for fragment in evidence
    ]
    return CaseProjection(
        caseId=case_id,
        asOfDate=as_of_date,
        facts=projected_facts,
        evidence=projected_evidence,
    )
