"""Current-copy applicability remains explicit through Core and Hermes projection."""

from dataclasses import replace
from datetime import date
from uuid import UUID

from agent_orchestrator.projection import build_case_projection

from legal_core.analysis import evidence_trace_sha256
from legal_core.api_contracts import LegalFragmentResponse, LegalLibraryDocumentResponse
from legal_core.legal_retrieval import ApprovedLegalLibraryDocument
from test_analysis import _evidence


def test_copy_basis_and_limits_are_transmitted_without_inventing_edition_dates():
    fragment = replace(
        _evidence(),
        issuer=None,
        version_date=None,
        publication_date=None,
        date_basis="LAWYER_CURRENT_COPY",
        extraction_limitations=["Synthetic limit"],
    )
    response = LegalFragmentResponse.model_validate(fragment, from_attributes=True)
    assert response.model_dump(by_alias=True)["dateBasis"] == "LAWYER_CURRENT_COPY"
    assert response.version_date is None and response.publication_date is None
    projection = build_case_projection(
        case_id=UUID(int=75), as_of_date=date(2026, 10, 8), facts={}, evidence=[fragment]
    )
    assert projection.evidence[0].date_basis == "LAWYER_CURRENT_COPY"
    assert projection.evidence[0].extraction_limitations == ["Synthetic limit"]


def test_current_copy_limits_are_bound_to_analysis_digest():
    fragment = replace(
        _evidence(), date_basis="LAWYER_CURRENT_COPY", extraction_limitations=["Synthetic limit"]
    )
    other = replace(fragment, extraction_limitations=["Changed synthetic limit"])
    assert evidence_trace_sha256([fragment], as_of_date=date(2026, 10, 8)) != (
        evidence_trace_sha256([other], as_of_date=date(2026, 10, 8))
    )


def test_approved_library_metadata_keeps_unknown_issuer_and_copy_semantics():
    fragment = _evidence()
    document = ApprovedLegalLibraryDocument(
        document_id=fragment.document_id,
        version_id=fragment.version_id,
        document_title=fragment.document_title,
        issuer=None,
        official_number=None,
        effective_from=fragment.effective_from,
        effective_to=None,
        source_url=fragment.source_url,
        raw_sha256=fragment.raw_sha256,
        fragment_count=1,
        date_basis="LAWYER_CURRENT_COPY",
        extraction_limitations=["Synthetic limit"],
    )
    response = LegalLibraryDocumentResponse.model_validate(document, from_attributes=True)
    assert response.issuer is None and response.date_basis == "LAWYER_CURRENT_COPY"
