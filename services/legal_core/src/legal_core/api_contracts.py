"""Validated REST contracts for Case Core."""

import json
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from legal_core.contracts import CaseStatus, ContractModel, FactKey, MissingFact


class CreateCaseRequest(ContractModel):
    intake_schema_version: Literal["dental-case-intake.v1"] = Field(alias="intakeSchemaVersion")
    channel: Literal["TELEGRAM"]


class CaseResponse(ContractModel):
    id: UUID
    public_number: str = Field(alias="publicNumber")
    status: CaseStatus
    intake_schema_version: str = Field(alias="intakeSchemaVersion")
    created_at: datetime = Field(alias="createdAt")


class ActorResponse(ContractModel):
    role: Literal["CLINIC_ADMIN"]


class LegalFragmentResponse(ContractModel):
    fragment_id: UUID = Field(alias="fragmentId")
    version_id: UUID = Field(alias="versionId")
    document_id: UUID = Field(alias="documentId")
    article: str | None
    part: str | None
    point: str | None
    structural_path: str = Field(alias="structuralPath")
    fragment_text: str = Field(alias="fragmentText")
    text_sha256: str = Field(alias="textSha256")
    effective_from: date = Field(alias="effectiveFrom")
    effective_to: date | None = Field(alias="effectiveTo")
    source_url: str = Field(alias="sourceUrl")
    raw_sha256: str = Field(alias="rawSha256")
    document_title: str = Field(alias="documentTitle")
    issuer: str
    official_number: str | None = Field(alias="officialNumber")
    version_date: date | None = Field(alias="versionDate")
    publication_date: date | None = Field(alias="publicationDate")


class LegalFragmentSearchResponse(ContractModel):
    items: list[LegalFragmentResponse]


class FactInput(ContractModel):
    fact_key: FactKey = Field(alias="factKey")
    value_type: Literal[
        "TEXT",
        "BOOLEAN",
        "DATE",
        "MONEY",
        "ENUM",
        "ENUM_SET",
        "DOCUMENT_INVENTORY",
    ] = Field(alias="valueType")
    value: dict[str, Any]
    source_type: Literal["USER_STATEMENT"] = Field(alias="sourceType")

    @field_validator("value")
    @classmethod
    def validate_value_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > 4_096:
            raise ValueError("fact value exceeds 4096 bytes")
        return value


class AddFactsRequest(ContractModel):
    question_id: str = Field(alias="questionId", min_length=1, max_length=80)
    intake_schema_version: Literal["dental-case-intake.v1"] = Field(alias="intakeSchemaVersion")
    facts: list[FactInput] = Field(min_length=1, max_length=20)


class IntakeResponse(ContractModel):
    case_id: UUID = Field(alias="caseId")
    status: CaseStatus
    missing_facts: list[MissingFact] = Field(alias="missingFacts")
    next_question_id: str | None = Field(alias="nextQuestionId")


class FinalizeRequest(ContractModel):
    pass


class CreateReportRequest(ContractModel):
    locale: Literal["ru-RU"] = "ru-RU"


class ReportResponse(ContractModel):
    id: UUID
    case_id: UUID = Field(alias="caseId")
    report_version: int = Field(alias="reportVersion")
    report_json: dict[str, Any] = Field(alias="reportJson")
    pdf_sha256: str = Field(alias="pdfSha256")
    created_at: datetime = Field(alias="createdAt")
