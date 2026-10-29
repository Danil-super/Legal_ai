"""Strict REST contracts for the trusted case-analysis orchestration boundary."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, RootModel, field_validator, model_validator
from pydantic_core import PydanticSerializationError

from legal_core.analysis_clarifications import ClarificationRequest, validate_unique_requests
from legal_core.case_preparation_contracts import (
    HumanReviewResult,
    LimitedResult,
    NeedsFactsResult,
    PreparationModel,
    PreparationResultBase,
)
from legal_core.contracts import (
    CanonicalReport, ContractModel, FactKey, LegalFragmentResponse, ReportResponse,
)
from legal_core.case_material_context_contracts import (
    CaseMaterialContextFragment, case_material_context_trace_sha256,
)
from legal_core.risk_engine import fact_snapshot_sha256
from legal_core.reference_analogy_contracts import (
    ReferenceAnalogyExample,
    build_reference_analogy_context,
)


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


class ClinicDocumentReadinessResponse(ContractModel):
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


class AnalysisContextResponse(ContractModel):
    case_id: UUID = Field(alias="caseId")
    as_of_date: date = Field(alias="asOfDate")
    facts: dict[str, Any]
    fact_snapshot_sha256: str = Field(alias="factSnapshotSha256", pattern=r"^[0-9a-f]{64}$")
    evidence_trace_sha256: str = Field(alias="evidenceTraceSha256", pattern=r"^[0-9a-f]{64}$")
    evidence: list[LegalFragmentResponse] = Field(min_length=1, max_length=30)
    clinic_document_context_trace_sha256: str = Field(
        alias="clinicDocumentContextTraceSha256", pattern=r"^[0-9a-f]{64}$"
    )
    clinic_document_context: list[ClinicDocumentContextResponse] = Field(
        default_factory=list, alias="clinicDocumentContext", max_length=20
    )
    clinic_document_readiness: list[ClinicDocumentReadinessResponse] = Field(
        default_factory=list,
        alias="clinicDocumentReadiness",
        max_length=20,
    )
    risk_policy_version: str = Field(alias="riskPolicyVersion", min_length=1, max_length=80)
    high_demand_threshold_kopecks: int = Field(alias="highDemandThresholdKopecks", ge=1)
    reference_examples: list[ReferenceAnalogyExample] | None = Field(
        default=None, alias="referenceExamples", min_length=1, max_length=3,
        exclude_if=lambda value: value is None,
    )
    reference_example_trace_sha256: str | None = Field(
        default=None, alias="referenceExampleTraceSha256", pattern=r"^[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )
    case_material_context: list[CaseMaterialContextFragment] | None = Field(
        default=None, alias="caseMaterialContext", min_length=1, max_length=12,
        exclude_if=lambda value: value is None,
    )
    case_material_context_trace_sha256: str | None = Field(
        default=None, alias="caseMaterialContextTraceSha256", pattern=r"^[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def exact_case_material_context(self) -> AnalysisContextResponse:
        if ((self.case_material_context is None)
                != (self.case_material_context_trace_sha256 is None)):
            raise ValueError("case material context requires its exact source trace")
        if (self.case_material_context is not None and case_material_context_trace_sha256(
                self.case_material_context) != self.case_material_context_trace_sha256):
            raise ValueError("case material context does not match its source trace")
        return self

    @model_validator(mode="after")
    def exact_optional_reference_context(self) -> AnalysisContextResponse:
        if (self.reference_examples is None) != (self.reference_example_trace_sha256 is None):
            raise ValueError("reference examples require their exact selection trace")
        if self.reference_examples is not None and build_reference_analogy_context(
            self.reference_examples,
        ).trace_sha256 != self.reference_example_trace_sha256:
            raise ValueError("reference examples differ from their selection trace")
        return self


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
    expected_reference_example_trace_sha256: str | None = Field(
        default=None, alias="expectedReferenceExampleTraceSha256", pattern=r"^[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )
    expected_case_material_context_trace_sha256: str | None = Field(
        default=None, alias="expectedCaseMaterialContextTraceSha256", pattern=r"^[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )
    claims: list[AnalysisClaimInput] = Field(max_length=30)
    semantic_reviews: list[SemanticReviewInput] = Field(
        alias="semanticReviews", max_length=30
    )
    clarification_requests: list[ClarificationRequest] = Field(
        default_factory=list, alias="clarificationRequests", max_length=3,
        exclude_if=lambda requests: not requests,
    )

    @field_validator("clarification_requests")
    @classmethod
    def clarification_ids_are_unique(
        cls, value: list[ClarificationRequest]
    ) -> list[ClarificationRequest]:
        return validate_unique_requests(value)

    @model_validator(mode="after")
    def reviews_reference_submitted_claims(self) -> AnalysisSubmissionRequest:
        claim_ids = {item.claim_id for item in self.claims}
        if any(item.claim_id not in claim_ids for item in self.semantic_reviews):
            raise ValueError("semantic reviews contain unknown claim identifiers")
        return self

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
    escalation_id: UUID | None = Field(default=None, alias="escalationId")
    clinic_document_readiness: list[ClinicDocumentReadinessResponse] = Field(
        default_factory=list,
        alias="clinicDocumentReadiness",
        max_length=20,
    )
    report: ReportResponse

    @model_validator(mode="after")
    def escalation_pointer_matches_requirement(self) -> AnalysisSubmissionResponse:
        if self.escalation_required and self.escalation_id is None:
            raise ValueError("an escalation-required analysis must include escalationId")
        if not self.escalation_required and self.escalation_id is not None:
            raise ValueError("a non-escalated analysis cannot include escalationId")
        return self


class ReportReadyResult(PreparationResultBase):
    """A typed, context-bound ready result; never an authorization or verifier substitute."""

    status: Literal["READY"]
    analysis_allowed: Literal[True] = Field(alias="analysisAllowed")
    report: ReportResponse

    @field_validator("analysis_allowed", mode="before")
    @classmethod
    def actual_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("ready analysis must be explicitly allowed")
        return value

    @field_validator("report", mode="before")
    @classmethod
    def revalidate_report(cls, value: object) -> object:
        return value.model_dump(by_alias=True) if isinstance(value, ReportResponse) else value

    @model_validator(mode="after")
    def ready_report_matches_source(self) -> ReportReadyResult:
        try:
            report_json = self.report.model_dump(mode="json", by_alias=True, warnings="error")
        except PydanticSerializationError as error:
            raise ValueError("invalid constructed report content") from error
        canonical = CanonicalReport.model_validate(report_json["reportJson"])
        context, analysis, risk = self.source.context, canonical.analysis, canonical.risk
        if context is None or analysis is None or risk is None:
            raise ValueError("ready result requires verified legal context")
        if (
            canonical.summary.analysis_availability.status != "READY"
            or analysis.verifier_status != "PASSED"
            or risk.level not in {"LOW", "MEDIUM"}
            or risk.escalation_required
            or canonical.clarification_requests or canonical.missing_facts
            or canonical.legal_basis.status != "AVAILABLE"
            or canonical.recommendations.status != "AVAILABLE"
        ):
            raise ValueError("ready report cannot be blocked, escalated, or awaiting facts")
        if (
            self.source.case_id != self.report.case_id
            or self.report.case_id != canonical.case.id
            or self.report.id != canonical.report_id
            or self.report.report_version != canonical.report_version
            or self.source.operation_id != analysis.analysis_run_id
            or self.source.fact_snapshot_sha256 != canonical.fact_snapshot_sha256
            or context.as_of_date != analysis.as_of_date
            or context.evidence_trace_sha256 != analysis.evidence_trace_sha256
            or context.clinic_document_context_trace_sha256
            != analysis.clinic_document_context_trace_sha256
            or context.case_material_context_trace_sha256
            != canonical.case_material_context_trace_sha256
            or context.risk_policy_version != risk.policy_version
        ):
            raise ValueError("ready report does not match the bound preparation source")
        if canonical.fact_snapshot_sha256 != fact_snapshot_sha256({
            FactKey(key): value for key, value in canonical.facts.items()
        }):
            raise ValueError("ready report facts do not match their fingerprint")
        if any(
            source.effective_from > context.as_of_date
            or (source.effective_to is not None and context.as_of_date >= source.effective_to)
            for source in canonical.legal_basis.sources
        ):
            raise ValueError("ready report sources must be applicable on the analysis date")
        return self


class PreparationResult(RootModel[Annotated[
    NeedsFactsResult | LimitedResult | HumanReviewResult | ReportReadyResult,
    Field(discriminator="status"),
]]):
    model_config = ConfigDict(revalidate_instances="always")


class PreparationJobResponse(PreparationModel):
    result_version: Literal[2] = Field(alias="resultVersion")
    job_id: UUID = Field(alias="jobId")
    case_id: UUID = Field(alias="caseId")
    state: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    error_code: str | None = Field(default=None, alias="errorCode", max_length=80)
    result: PreparationResult | None = None

    @field_validator("result_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("unsupported preparation job version")
        return value

    @model_validator(mode="after")
    def coherent_result(self) -> PreparationJobResponse:
        if (self.state == "SUCCEEDED") != (self.result is not None):
            raise ValueError("only a completed job can contain a result")
        if self.result is not None and self.result.root.source.case_id != self.case_id:
            raise ValueError("preparation result case mismatch")
        if self.state == "SUCCEEDED" and self.error_code is not None:
            raise ValueError("successful preparation cannot include a failure")
        return self
