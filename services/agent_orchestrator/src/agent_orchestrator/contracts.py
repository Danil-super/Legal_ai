"""Strict JSON contracts crossing the Legal Core <-> Hermes boundary."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from legal_core.analysis_clarifications import ClarificationRequest, validate_unique_requests
from legal_core.contracts import FactKey
from legal_core.pseudonymization import pseudonymize_text


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EvidenceItem(StrictModel):
    fragment_id: UUID = Field(alias="fragmentId")
    document_title: str = Field(alias="documentTitle", min_length=1, max_length=500)
    official_number: str | None = Field(default=None, alias="officialNumber", max_length=100)
    structural_path: str = Field(alias="structuralPath", min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=12_000)
    effective_from: date = Field(alias="effectiveFrom")
    effective_to: date | None = Field(default=None, alias="effectiveTo")
    source_url: str = Field(alias="sourceUrl", min_length=1, max_length=2_000)
    date_basis: Literal["DATED_EDITION", "LAWYER_CURRENT_COPY"] = Field(
        default="DATED_EDITION", alias="dateBasis"
    )
    extraction_limitations: list[str] = Field(
        default_factory=list, alias="extractionLimitations", max_length=40
    )


class ClinicDocumentContextItem(StrictModel):
    """Clinic-owned context that is explicitly non-citable and not legal evidence."""

    context_kind: Literal["CLINIC_DOCUMENT_CONTEXT"] = Field(
        default="CLINIC_DOCUMENT_CONTEXT", alias="contextKind"
    )
    document_type: str = Field(alias="documentType", min_length=1, max_length=80)
    document_title: str = Field(alias="documentTitle", min_length=1, max_length=240)
    version_no: int = Field(alias="versionNo", ge=1)
    valid_from: date | None = Field(default=None, alias="validFrom")
    valid_to: date | None = Field(default=None, alias="validTo")
    structural_path: str = Field(alias="structuralPath", min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=12_000)
    conflict_hints: list[str] = Field(
        default_factory=list,
        alias="conflictHints",
        max_length=10,
    )


class ClinicDocumentReadinessItem(StrictModel):
    """Non-blocking operational checklist; never a legal requirement or evidence."""

    expectation_code: str = Field(alias="expectationCode", min_length=1, max_length=80)
    importance: Literal["CORE", "SCENARIO", "SUPPORTING"]
    accepted_document_types: list[str] = Field(
        alias="acceptedDocumentTypes", min_length=1, max_length=10
    )
    reason_code: str = Field(alias="reasonCode", min_length=1, max_length=100)
    status: Literal["RETRIEVED", "AVAILABLE_NOT_RETRIEVED", "NOT_AVAILABLE"]
    matched_document_keys: list[str] = Field(
        default_factory=list,
        alias="matchedDocumentKeys",
        max_length=20,
    )
    analysis_blocking: Literal[False] = Field(default=False, alias="analysisBlocking")


class ReferenceAnalogyItem(StrictModel):
    """Model-visible comparison only; excludes private database provenance."""

    model_config = ConfigDict(frozen=True, revalidate_instances="always", hide_input_in_errors=True)
    analogy_id: str = Field(alias="analogyId", pattern=r"^example-[1-3]$")
    purpose: Literal["NON_AUTHORITATIVE_ANALOGY"] = "NON_AUTHORITATIVE_ANALOGY"
    date_basis: Literal["EVALUATION_DATE_NOT_EVENT_DATE"] = Field(
        default="EVALUATION_DATE_NOT_EVENT_DATE", alias="dateBasis",
    )
    group_key: Literal["clinical", "labour", "courts", "privacy", "licensing",
                       "healthcare", "general"] = Field(alias="groupKey")
    evaluation_as_of_date: date = Field(alias="evaluationAsOfDate")
    scenario_text: str = Field(alias="scenarioText", min_length=10, max_length=12_000)

    @field_validator("scenario_text")
    @classmethod
    def deidentified_text(cls, value: str) -> str:
        if not value.strip() or pseudonymize_text(value).changed:
            raise ValueError("analogy text must be de-identified")
        return value


class CaseMaterialContextItem(StrictModel):
    """Anonymized user-supplied excerpts without private source identifiers."""

    model_config = ConfigDict(frozen=True, revalidate_instances="always", hide_input_in_errors=True)
    context_kind: Literal["CASE_MATERIAL_CONTEXT"] = Field(
        default="CASE_MATERIAL_CONTEXT", alias="contextKind",
    )
    display_name: str = Field(alias="displayName", pattern=r"^Материал (?:[1-9]|10)$")
    structural_path: str = Field(alias="structuralPath", min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=1_200)
    extraction_limitations: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(
        default_factory=list, alias="extractionLimitations", max_length=8,
    )

    @field_validator("text", "structural_path", "extraction_limitations")
    @classmethod
    def deidentified_context(cls, value: str | list[str]) -> str | list[str]:
        values = [value] if isinstance(value, str) else value
        if any(not item.strip() or pseudonymize_text(item).changed for item in values):
            raise ValueError("case material context must be bounded and de-identified")
        return value


class CaseProjection(StrictModel):
    """Minimal pseudonymous input that may be sent to a reasoning provider."""

    case_id: UUID = Field(alias="caseId")
    as_of_date: date = Field(alias="asOfDate")
    facts: dict[str, Any]
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=30)
    clinic_document_context: list[ClinicDocumentContextItem] = Field(
        default_factory=list, alias="clinicDocumentContext", max_length=20
    )
    clinic_document_readiness: list[ClinicDocumentReadinessItem] = Field(
        default_factory=list,
        alias="clinicDocumentReadiness",
        max_length=20,
    )
    case_material_context: list[CaseMaterialContextItem] | None = Field(
        default=None, alias="caseMaterialContext", min_length=1, max_length=12,
        exclude_if=lambda value: value is None,
    )
    reference_examples: list[ReferenceAnalogyItem] = Field(
        default_factory=list, alias="referenceExamples", max_length=3,
        exclude_if=lambda value: not value,
    )

    @model_validator(mode="after")
    def bounded_analogies(self) -> CaseProjection:
        if (len({item.analogy_id for item in self.reference_examples})
                != len(self.reference_examples)
                or sum(len(item.scenario_text) for item in self.reference_examples) > 12_000):
            raise ValueError("analogy selection exceeds its complete-example budget")
        return self


class ClaimProposal(StrictModel):
    claim_id: str = Field(alias="claimId", min_length=1, max_length=80)
    kind: Literal["LEGAL", "ACTION"]
    text: str = Field(min_length=1, max_length=4_000)
    evidence_fragment_ids: list[UUID] = Field(
        alias="evidenceFragmentIds", min_length=1, max_length=10
    )
    required_fact_keys: list[FactKey] = Field(
        default_factory=list, alias="requiredFactKeys", max_length=20
    )

    @field_validator("evidence_fragment_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("evidence fragment ids must be unique")
        return value

    @field_validator("required_fact_keys")
    @classmethod
    def unique_fact_keys(cls, value: list[FactKey]) -> list[FactKey]:
        if len(value) != len(set(value)):
            raise ValueError("required fact keys must be unique")
        return value


class ClaimProposalBatch(StrictModel):
    claims: list[ClaimProposal] = Field(max_length=30)
    internal_recommendations: list[str] = Field(
        default_factory=list, alias="internalRecommendations", max_length=20
    )
    patient_draft: str | None = Field(default=None, alias="patientDraft", max_length=8_000)
    clarification_requests: list[ClarificationRequest] = Field(
        default_factory=list, alias="clarificationRequests", max_length=3
    )

    @field_validator("clarification_requests")
    @classmethod
    def unique_clarification_requests(
        cls, value: list[ClarificationRequest]
    ) -> list[ClarificationRequest]:
        return validate_unique_requests(value)

    @field_validator("claims")
    @classmethod
    def unique_claim_ids(cls, value: list[ClaimProposal]) -> list[ClaimProposal]:
        identifiers = [claim.claim_id for claim in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("claim ids must be unique")
        return value


class SemanticReviewItem(StrictModel):
    claim_id: str = Field(alias="claimId", min_length=1, max_length=80)
    verdict: Literal["SUPPORTED", "UNSUPPORTED", "CONTRADICTED"]
    reviewed_fragment_ids: list[UUID] = Field(
        alias="reviewedFragmentIds", min_length=1, max_length=10
    )

    @field_validator("reviewed_fragment_ids")
    @classmethod
    def unique_reviewed_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("reviewed fragment ids must be unique")
        return value


class SemanticReviewBatch(StrictModel):
    reviews: list[SemanticReviewItem] = Field(min_length=1, max_length=30)

    @field_validator("reviews")
    @classmethod
    def unique_review_claim_ids(cls, value: list[SemanticReviewItem]) -> list[SemanticReviewItem]:
        identifiers = [review.claim_id for review in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("semantic review claim ids must be unique")
        return value
