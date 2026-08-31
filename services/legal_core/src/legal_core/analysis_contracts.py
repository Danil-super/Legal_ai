"""Strict REST contracts for the trusted case-analysis orchestration boundary."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from legal_core.api_contracts import LegalFragmentResponse, ReportResponse
from legal_core.contracts import ContractModel, FactKey


class ClinicDocumentContextResponse(ContractModel):
    context_kind: Literal["CLINIC_DOCUMENT_CONTEXT"] = Field(
        default="CLINIC_DOCUMENT_CONTEXT", alias="contextKind"
    )
    fragment_id: UUID = Field(alias="fragmentId")
    version_id: UUID = Field(alias="versionId")
    document_id: UUID = Field(alias="documentId")
    document_key: str = Field(alias="documentKey", min_length=1, max_length=100)
    document_type: str = Field(alias="documentType", min_length=1, max_length=80)
    document_title: str = Field(alias="documentTitle", min_length=1, max_length=240)
    version_no: int = Field(alias="versionNo", ge=1)
    valid_from: date | None = Field(default=None, alias="validFrom")
    valid_to: date | None = Field(default=None, alias="validTo")
    structural_path: str = Field(alias="structuralPath", min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=12_000)
    text_sha256: str = Field(alias="textSha256", pattern=r"^[0-9a-f]{64}$")
    raw_sha256: str = Field(alias="rawSha256", pattern=r"^[0-9a-f]{64}$")


class AnalysisContextResponse(ContractModel):
    case_id: UUID = Field(alias="caseId")
    as_of_date: date = Field(alias="asOfDate")
    facts: dict[str, Any]
    fact_snapshot_sha256: str = Field(
        alias="factSnapshotSha256", pattern=r"^[0-9a-f]{64}$"
    )
    evidence_trace_sha256: str = Field(
        alias="evidenceTraceSha256", pattern=r"^[0-9a-f]{64}$"
    )
    evidence: list[LegalFragmentResponse] = Field(min_length=1, max_length=30)
    clinic_document_context_trace_sha256: str = Field(
        alias="clinicDocumentContextTraceSha256", pattern=r"^[0-9a-f]{64}$"
    )
    clinic_document_context: list[ClinicDocumentContextResponse] = Field(
        default_factory=list, alias="clinicDocumentContext", max_length=20
    )
    risk_policy_version: str = Field(alias="riskPolicyVersion", min_length=1, max_length=80)
    high_demand_threshold_kopecks: int = Field(
        alias="highDemandThresholdKopecks", ge=1
    )


class AnalysisClaimInput(ContractModel):
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
    def evidence_ids_are_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("evidence fragment ids must be unique")
        return value

    @field_validator("required_fact_keys")
    @classmethod
    def fact_keys_are_unique(cls, value: list[FactKey]) -> list[FactKey]:
        if len(value) != len(set(value)):
            raise ValueError("required fact keys must be unique")
        return value


class SemanticReviewInput(ContractModel):
    claim_id: str = Field(alias="claimId", min_length=1, max_length=80)
    verdict: Literal["SUPPORTED", "UNSUPPORTED", "CONTRADICTED"]
    reviewed_fragment_ids: list[UUID] = Field(
        alias="reviewedFragmentIds", min_length=1, max_length=10
    )

    @field_validator("reviewed_fragment_ids")
    @classmethod
    def reviewed_ids_are_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("reviewed fragment ids must be unique")
        return value


class AnalysisSubmissionRequest(ContractModel):
    as_of_date: date = Field(alias="asOfDate")
    expected_fact_snapshot_sha256: str = Field(
        alias="expectedFactSnapshotSha256", pattern=r"^[0-9a-f]{64}$"
    )
    expected_evidence_trace_sha256: str = Field(
        alias="expectedEvidenceTraceSha256", pattern=r"^[0-9a-f]{64}$"
    )
    expected_clinic_document_context_trace_sha256: str = Field(
        alias="expectedClinicDocumentContextTraceSha256", pattern=r"^[0-9a-f]{64}$"
    )
    expected_risk_policy_version: str = Field(
        alias="expectedRiskPolicyVersion", min_length=1, max_length=80
    )
    claims: list[AnalysisClaimInput] = Field(min_length=1, max_length=30)
    semantic_reviews: list[SemanticReviewInput] = Field(
        alias="semanticReviews", min_length=1, max_length=30
    )

    @field_validator("claims")
    @classmethod
    def claim_ids_are_unique(cls, value: list[AnalysisClaimInput]) -> list[AnalysisClaimInput]:
        identifiers = [item.claim_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("claim ids must be unique")
        return value

    @field_validator("semantic_reviews")
    @classmethod
    def review_ids_are_unique(cls, value: list[SemanticReviewInput]) -> list[SemanticReviewInput]:
        identifiers = [item.claim_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("semantic review claim ids must be unique")
        return value


class AnalysisSubmissionResponse(ContractModel):
    analysis_allowed: bool = Field(alias="analysisAllowed")
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNAVAILABLE"] = Field(
        alias="riskLevel"
    )
    escalation_required: bool = Field(alias="escalationRequired")
    report: ReportResponse