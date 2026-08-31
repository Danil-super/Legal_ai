"""Strict JSON contracts crossing the Legal Core <-> Hermes boundary."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class CaseProjection(StrictModel):
    """Minimal pseudonymous input that may be sent to a reasoning provider."""

    case_id: UUID = Field(alias="caseId")
    as_of_date: date = Field(alias="asOfDate")
    facts: dict[str, Any]
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=30)


class ClaimProposal(StrictModel):
    claim_id: str = Field(alias="claimId", min_length=1, max_length=80)
    kind: Literal["LEGAL", "ACTION"]
    text: str = Field(min_length=1, max_length=4_000)
    evidence_fragment_ids: list[UUID] = Field(
        alias="evidenceFragmentIds", min_length=1, max_length=10
    )
    required_fact_keys: list[str] = Field(
        default_factory=list, alias="requiredFactKeys", max_length=20
    )

    @field_validator("evidence_fragment_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("evidence fragment ids must be unique")
        return value


class ClaimProposalBatch(StrictModel):
    claims: list[ClaimProposal] = Field(min_length=1, max_length=30)
    internal_recommendations: list[str] = Field(
        default_factory=list, alias="internalRecommendations", max_length=20
    )
    patient_draft: str | None = Field(default=None, alias="patientDraft", max_length=8_000)

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
