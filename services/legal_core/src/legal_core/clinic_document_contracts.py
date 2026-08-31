"""REST contracts for tenant-owned clinic document ingestion and retrieval."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from legal_core.contracts import ContractModel

ClinicDocumentDecision = Literal["APPROVED", "RETIRED", "BLOCKED"]


class CreateClinicDocumentRequest(ContractModel):
    document_key: str = Field(
        alias="documentKey",
        min_length=3,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9._-]{2,99}$",
    )
    document_type: str = Field(
        alias="documentType",
        min_length=3,
        max_length=80,
        pattern=r"^[A-Z0-9_]{3,80}$",
    )
    title: str = Field(min_length=1, max_length=240)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped


class ClinicDocumentResponse(ContractModel):
    id: UUID
    document_key: str = Field(alias="documentKey")
    document_type: str = Field(alias="documentType")
    title: str
    created_at: datetime = Field(alias="createdAt")


class ClinicDocumentListResponse(ContractModel):
    items: list[ClinicDocumentResponse]


class CreateClinicDocumentTextVersionRequest(ContractModel):
    source_filename: str = Field(default="manual.txt", alias="sourceFilename", max_length=255)
    normalized_text: str = Field(alias="normalizedText", min_length=1, max_length=200_000)
    valid_from: date | None = Field(default=None, alias="validFrom")
    valid_to: date | None = Field(default=None, alias="validTo")

    @field_validator("source_filename")
    @classmethod
    def strip_source_filename(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("sourceFilename must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_dates(self) -> "CreateClinicDocumentTextVersionRequest":
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_to <= self.valid_from:
                raise ValueError("validTo must be later than validFrom")
        return self


class ClinicDocumentVersionResponse(ContractModel):
    id: UUID
    document_id: UUID = Field(alias="documentId")
    version_no: int = Field(alias="versionNo")
    source_filename: str = Field(alias="sourceFilename")
    mime_type: Literal["text/plain"] = Field(alias="mimeType")
    raw_sha256: str = Field(alias="rawSha256")
    normalized_text_sha256: str = Field(alias="normalizedTextSha256")
    fragment_count: int = Field(alias="fragmentCount")
    valid_from: date | None = Field(alias="validFrom")
    valid_to: date | None = Field(alias="validTo")
    created_at: datetime = Field(alias="createdAt")


class ClinicDocumentApprovalRequest(ContractModel):
    decision: ClinicDocumentDecision
    reason_code: str = Field(
        alias="reasonCode",
        min_length=3,
        max_length=80,
        pattern=r"^[A-Z0-9_]{3,80}$",
    )


class ClinicDocumentApprovalResponse(ContractModel):
    id: UUID
    version_id: UUID = Field(alias="versionId")
    decision: ClinicDocumentDecision
    reason_code: str = Field(alias="reasonCode")
    created_at: datetime = Field(alias="createdAt")


class ClinicDocumentFragmentResponse(ContractModel):
    fragment_id: UUID = Field(alias="fragmentId")
    version_id: UUID = Field(alias="versionId")
    document_id: UUID = Field(alias="documentId")
    document_key: str = Field(alias="documentKey")
    document_type: str = Field(alias="documentType")
    document_title: str = Field(alias="documentTitle")
    version_no: int = Field(alias="versionNo")
    valid_from: date | None = Field(alias="validFrom")
    valid_to: date | None = Field(alias="validTo")
    ordinal: int
    structural_path: str = Field(alias="structuralPath")
    fragment_text: str = Field(alias="fragmentText")
    text_sha256: str = Field(alias="textSha256")


class ClinicDocumentFragmentSearchResponse(ContractModel):
    items: list[ClinicDocumentFragmentResponse]
