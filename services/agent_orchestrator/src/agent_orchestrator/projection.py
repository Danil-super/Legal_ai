"""Build the only case projection allowed to cross into Hermes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from agent_orchestrator.contracts import (
    CaseProjection,
    ClinicDocumentContextItem,
    EvidenceItem,
)
from legal_core.analysis_contracts import AnalysisContextResponse
from legal_core.clinic_document_conflicts import detect_potential_clinic_document_conflicts
from legal_core.clinic_document_retrieval import ApprovedClinicDocumentFragment
from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment
from legal_core.pseudonymization import (
    contains_obvious_direct_identifier,
    pseudonymize_text,
)


def _pseudonymize_value(
    value: object,
    *,
    known_identifiers: dict[str, str],
) -> Any:
    if isinstance(value, str):
        redacted = pseudonymize_text(value, known_identifiers=known_identifiers).text
        if contains_obvious_direct_identifier(redacted):
            message = "case fact still contains an obvious direct identifier after redaction"
            raise ValueError(message)
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


def _redact_clinic_text(value: str, *, known_identifiers: dict[str, str]) -> str:
    redacted = pseudonymize_text(value, known_identifiers=known_identifiers).text
    if contains_obvious_direct_identifier(redacted):
        raise ValueError("clinic document context contains a direct identifier after redaction")
    return redacted


def _conflict_reason_codes(text: str) -> list[str]:
    return [
        hint.reason_code
        for hint in detect_potential_clinic_document_conflicts(text)
        if hint.review_required
    ]


def _clinic_context_item(
    fragment: ApprovedClinicDocumentFragment,
    *,
    known_identifiers: dict[str, str],
) -> ClinicDocumentContextItem:
    redacted_text = _redact_clinic_text(
        fragment.fragment_text,
        known_identifiers=known_identifiers,
    )
    return ClinicDocumentContextItem(
        documentType=fragment.document_type,
        documentTitle=_redact_clinic_text(
            fragment.document_title,
            known_identifiers=known_identifiers,
        ),
        versionNo=fragment.version_no,
        validFrom=fragment.valid_from,
        validTo=fragment.valid_to,
        structuralPath=fragment.structural_path,
        text=redacted_text,
        conflictHints=_conflict_reason_codes(redacted_text),
    )


def build_case_projection(
    *,
    case_id: UUID,
    as_of_date: date,
    facts: Mapping[FactKey, object],
    evidence: Sequence[ApprovedLegalFragment],
    clinic_document_context: Sequence[ApprovedClinicDocumentFragment] = (),
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
    projected_clinic_context = [
        _clinic_context_item(fragment, known_identifiers=identifiers)
        for fragment in clinic_document_context
    ]
    return CaseProjection(
        caseId=case_id,
        asOfDate=as_of_date,
        facts=projected_facts,
        evidence=projected_evidence,
        clinicDocumentContext=projected_clinic_context,
    )


def build_projection_from_context(
    context: AnalysisContextResponse,
    *,
    known_identifiers: dict[str, str] | None = None,
) -> CaseProjection:
    """Redact a typed Legal Core response before the first external model call."""

    identifiers = known_identifiers or {}
    projected_facts = {
        key: _pseudonymize_value(value, known_identifiers=identifiers)
        for key, value in sorted(context.facts.items())
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
        for fragment in context.evidence
    ]
    projected_clinic_context: list[ClinicDocumentContextItem] = []
    for fragment in context.clinic_document_context:
        redacted_text = _redact_clinic_text(
            fragment.text,
            known_identifiers=identifiers,
        )
        projected_clinic_context.append(
            ClinicDocumentContextItem(
                documentType=fragment.document_type,
                documentTitle=_redact_clinic_text(
                    fragment.document_title,
                    known_identifiers=identifiers,
                ),
                versionNo=fragment.version_no,
                validFrom=fragment.valid_from,
                validTo=fragment.valid_to,
                structuralPath=fragment.structural_path,
                text=redacted_text,
                conflictHints=_conflict_reason_codes(redacted_text),
            )
        )
    return CaseProjection(
        caseId=context.case_id,
        asOfDate=context.as_of_date,
        facts=projected_facts,
        evidence=projected_evidence,
        clinicDocumentContext=projected_clinic_context,
    )
