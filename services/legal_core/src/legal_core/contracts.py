"""Versioned public contracts for case intake and reports."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    status: Literal["BLOCKED"] = "BLOCKED"
    reason_code: Literal["LEGAL_CORPUS_NOT_READY"] = Field(
        default="LEGAL_CORPUS_NOT_READY", alias="reasonCode"
    )


class ReportCase(ContractModel):
    id: UUID
    public_number: str = Field(alias="publicNumber")
    status: CaseStatus


class ReportSummary(ContractModel):
    neutral_description: str = Field(alias="neutralDescription")
    incident_types: list[str] = Field(alias="incidentTypes")
    analysis_availability: AnalysisAvailability = Field(alias="analysisAvailability")


class Recommendations(ContractModel):
    status: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    items: list[str] = Field(default_factory=list)


class DraftResponse(ContractModel):
    status: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    text: None = None
    is_draft: Literal[True] = Field(default=True, alias="isDraft")
    human_approval_required: Literal[True] = Field(
        default=True, alias="humanApprovalRequired"
    )


class LegalBasis(ContractModel):
    status: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    sources: list[dict[str, Any]] = Field(default_factory=list)


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
    fact_snapshot_sha256: str = Field(alias="factSnapshotSha256", pattern=r"^[0-9a-f]{64}$")
    disclaimer: str

