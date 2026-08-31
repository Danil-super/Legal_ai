"""Strict REST contracts for the trusted case-analysis orchestration boundary."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from legal_core.api_contracts import LegalFragmentResponse, ReportResponse
from legal_core.contracts import ContractModel, FactKey


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
