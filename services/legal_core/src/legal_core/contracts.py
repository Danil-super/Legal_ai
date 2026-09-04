"""Versioned public contracts for case intake and evidence-gated reports."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CaseStatus(StrEnum):
    COLLECTING = "COLLECTING"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    INTAKE_COMPLETE = "INTAKE_COMPLETE"
    ANALYSIS_BLOCKED = "ANALYSIS_BLOCKED"
    READY_FOR_ANALYSIS = "READY_FOR_ANALYSIS"
    ANALYZING = "ANALYZING"
    REPORT_READY = "REPORT_READY"
    ESCALATION_REQUIRED = "ESCALATION_REQUIRED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    CONTENT_PURGED = "CONTENT_PURGED"


class FactKey(StrEnum):
    INCIDENT_TYPES = "INCIDENT_TYPES"
    PRIMARY_INCIDENT_TYPE = "PRIMARY_INCIDENT_TYPE"
    SERVICE_TYPE = "SERVICE_TYPE"
    SERVICE_DATE = "SERVICE_DATE"
    INCIDENT_DATE = "INCIDENT_DATE"
    CLAIM_DATE = "CLAIM_DATE"
    PROBLEM_SUMMARY = "PROBLEM_SUMMARY"
    PATIENT_DEMAND = "PATIENT_DEMAND"
    DEMAND_AMOUNT = "DEMAND_AMOUNT"
    FORMAL_CLAIM = "FORMAL_CLAIM"
    CLAIM_RECEIVED_AT = "CLAIM_RECEIVED_AT"
    RESPONSE_DEADLINE = "RESPONSE_DEADLINE"
    HARM_CLAIMED = "HARM_CLAIMED"
    HOSPITALIZATION = "HOSPITALIZATION"
    LAWYER_CONTACT = "LAWYER_CONTACT"
    REPRESENTATIVE_AUTHORITY = "REPRESENTATIVE_AUTHORITY"
    REGULATOR_OR_COURT = "REGULATOR_OR_COURT"
    REGULATOR_THREAT = "REGULATOR_THREAT"
    AUTHORITY_KIND = "AUTHORITY_KIND"
    DOCUMENT_DATE = "DOCUMENT_DATE"
    CLINIC_DOCUMENTS = "CLINIC_DOCUMENTS"


class MissingFactSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"


class MissingFact(ContractModel):
    fact_key: FactKey = Field(alias="factKey")
    severity: MissingFactSeverity
    reason_code: str = Field(alias="reasonCode")
    question_id: str = Field(alias="questionId")


class AnalysisAvailability(ContractModel):
    status: Literal["BLOCKED", "READY"] = "BLOCKED"
    reason_code: str | None = Field(default="LEGAL_CORPUS_NOT_READY", alias="reasonCode")

    @model_validator(mode="after")
    def validate_state(self) -> "AnalysisAvailability":
        if self.status == "READY" and self.reason_code is not None:
            raise ValueError("READY analysis cannot have a block reason")
        if self.status == "BLOCKED" and not self.reason_code:
            raise ValueError("BLOCKED analysis requires a reason code")
        return self


class ReportCase(ContractModel):
    id: UUID
    public_number: str = Field(alias="publicNumber")
    status: CaseStatus


class ReportSummary(ContractModel):
    neutral_description: str = Field(alias="neutralDescription")
    incident_types: list[str] = Field(alias="incidentTypes")
    analysis_availability: AnalysisAvailability = Field(alias="analysisAvailability")


class Recommendations(ContractModel):
    status: Literal["NOT_AVAILABLE", "AVAILABLE"] = "NOT_AVAILABLE"
    items: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_state(self) -> "Recommendations":
        if self.status == "AVAILABLE" and not self.items:
            raise ValueError("AVAILABLE recommendations require at least one item")
        if self.status == "NOT_AVAILABLE" and self.items:
            raise ValueError("NOT_AVAILABLE recommendations cannot contain items")
        return self


class DraftResponse(ContractModel):
    status: Literal["NOT_AVAILABLE", "AVAILABLE", "BLOCKED"] = "NOT_AVAILABLE"
    text: str | None = Field(default=None, max_length=8_000)
    is_draft: Literal[True] = Field(default=True, alias="isDraft")
    human_approval_required: Literal[True] = Field(
        default=True, alias="humanApprovalRequired"
    )
    reason_code: str | None = Field(default=None, alias="reasonCode")
    policy_version: str | None = Field(default=None, alias="policyVersion", max_length=80)

    @model_validator(mode="after")
    def validate_state(self) -> "DraftResponse":
        if self.status == "AVAILABLE":
            if not self.text or self.reason_code is not None:
                raise ValueError("AVAILABLE draft requires text and no block reason")
        elif self.text is not None:
            raise ValueError("unavailable/blocked draft cannot contain text")
        if self.status == "BLOCKED" and not self.reason_code:
            raise ValueError("BLOCKED draft requires a reason code")
        return self


class LegalSourceCard(ContractModel):
    fragment_id: UUID = Field(alias="fragmentId")
    document_title: str = Field(alias="documentTitle")
    official_number: str | None = Field(default=None, alias="officialNumber")
    structural_path: str = Field(alias="structuralPath")
    effective_from: date = Field(alias="effectiveFrom")
    effective_to: date | None = Field(default=None, alias="effectiveTo")
    source_url: str = Field(alias="sourceUrl")
    text_sha256: str = Field(alias="textSha256", pattern=r"^[0-9a-f]{64}$")
    raw_sha256: str = Field(alias="rawSha256", pattern=r"^[0-9a-f]{64}$")


class LegalBasis(ContractModel):
    status: Literal["NOT_AVAILABLE", "AVAILABLE"] = "NOT_AVAILABLE"
    sources: list[LegalSourceCard] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_state(self) -> "LegalBasis":
        if self.status == "AVAILABLE" and not self.sources:
            raise ValueError("AVAILABLE legal basis requires sources")
        if self.status == "NOT_AVAILABLE" and self.sources:
            raise ValueError("NOT_AVAILABLE legal basis cannot contain sources")
        return self


class ClinicDocumentSourceCard(ContractModel):
    fragment_id: UUID = Field(alias="fragmentId")
    version_id: UUID = Field(alias="versionId")
    document_id: UUID = Field(alias="documentId")
    document_key: str = Field(alias="documentKey", min_length=1, max_length=100)
    document_type: str = Field(alias="documentType", min_length=1, max_length=80)
    document_title: str = Field(alias="documentTitle", min_length=1, max_length=240)
    version_no: int = Field(alias="versionNo", ge=1)
    valid_from: date | None = Field(alias="validFrom")
    valid_to: date | None = Field(alias="validTo")
    structural_path: str = Field(alias="structuralPath", min_length=1, max_length=500)
    text_sha256: str = Field(alias="textSha256", pattern=r"^[0-9a-f]{64}$")
    raw_sha256: str = Field(alias="rawSha256", pattern=r"^[0-9a-f]{64}$")


class ClinicDocumentBasis(ContractModel):
    status: Literal["NOT_USED", "USED"] = "NOT_USED"
    sources: list[ClinicDocumentSourceCard] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_state(self) -> "ClinicDocumentBasis":
        if self.status == "USED" and not self.sources:
            raise ValueError("USED clinic document context requires sources")
        if self.status == "NOT_USED" and self.sources:
            raise ValueError("NOT_USED clinic document context cannot contain sources")
        return self


class ClinicDocumentReadinessCard(ContractModel):
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


class RiskSummary(ContractModel):
    level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNAVAILABLE"]
    reason_codes: list[str] = Field(alias="reasonCodes", max_length=30)
    policy_version: str = Field(alias="policyVersion", min_length=1, max_length=80)
    escalation_required: bool = Field(alias="escalationRequired")


class AnalysisSnapshot(ContractModel):
    analysis_run_id: UUID = Field(alias="analysisRunId")
    as_of_date: date = Field(alias="asOfDate")
    verifier_status: Literal["PASSED", "BLOCKED"] = Field(alias="verifierStatus")
    evidence_trace_sha256: str = Field(
        alias="evidenceTraceSha256", pattern=r"^[0-9a-f]{64}$"
    )
    clinic_document_context_trace_sha256: str | None = Field(
        default=None,
        alias="clinicDocumentContextTraceSha256",
        pattern=r"^[0-9a-f]{64}$",
    )


class CanonicalReport(ContractModel):
    schema_version: Literal["dental-case-report.v1"] = Field(
        default="dental-case-report.v1", alias="schemaVersion"
    )
    report_id: UUID = Field(alias="reportId")
    report_version: int = Field(alias="reportVersion", ge=1)
    generated_at: datetime = Field(alias="generatedAt")
    case: ReportCase
    summary: ReportSummary
    facts: dict[str, Any]
    missing_facts: list[MissingFact] = Field(alias="missingFacts")
    recommendations: Recommendations
    draft_response: DraftResponse = Field(alias="draftResponse")
    legal_basis: LegalBasis = Field(alias="legalBasis")
    clinic_documents: ClinicDocumentBasis = Field(
        default_factory=ClinicDocumentBasis,
        alias="clinicDocuments",
    )
    clinic_document_readiness: list[ClinicDocumentReadinessCard] = Field(
        default_factory=list,
        alias="clinicDocumentReadiness",
        max_length=20,
    )
    risk: RiskSummary | None = None
    analysis: AnalysisSnapshot | None = None
    fact_snapshot_sha256: str = Field(alias="factSnapshotSha256", pattern=r"^[0-9a-f]{64}$")
    disclaimer: str

    @model_validator(mode="after")
    def validate_analysis_consistency(self) -> "CanonicalReport":
        ready = self.summary.analysis_availability.status == "READY"
        if ready and (self.risk is None or self.analysis is None):
            raise ValueError("READY report requires risk and analysis snapshots")
        if not ready and (self.risk is not None or self.analysis is not None):
            raise ValueError("BLOCKED intake report cannot contain analysis snapshots")
        if not ready and self.clinic_documents.status != "NOT_USED":
            raise ValueError("BLOCKED intake report cannot expose clinic document analysis context")
        return self
