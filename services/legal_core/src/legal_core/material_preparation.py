"""Unapproved, checksum-bound preparation of privately supplied originals."""

import hashlib
import json
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from legal_core.contracts import ContractModel

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
EvidenceLocator = Annotated[str, Field(min_length=3, max_length=1000)]
MaterialKind = Literal["NORMATIVE", "CLINICAL_REFERENCE", "REFERENCE_FORM"]
MaterialGroup = Literal[
    "clinical", "labour", "courts", "privacy", "licensing", "healthcare", "general"
]


class PreparedPart(ContractModel):
    """Known identity fields, not a licence to invent missing version metadata."""

    part_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1, max_length=1000)
    canonical_key: str | None = Field(default=None, min_length=1, max_length=120)
    document_type: str | None = Field(default=None, min_length=1, max_length=50)
    issuer: str | None = Field(default=None, min_length=1, max_length=240)
    official_number: str | None = Field(default=None, min_length=1, max_length=80)
    adoption_date: date | None = None
    publication_date: date | None = None
    version_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    evidence: dict[str, EvidenceLocator] = Field(default_factory=dict, max_length=16)

    def missing_fields(self) -> list[str]:
        return [name for name in (
            "canonical_key", "document_type", "issuer", "official_number",
            "adoption_date", "publication_date", "version_date", "effective_from",
        ) if getattr(self, name) is None]

    @model_validator(mode="after")
    def dates_have_provenance(self) -> "PreparedPart":
        fields = {"adoption_date", "publication_date", "version_date",
                  "effective_from", "effective_to"}
        if not set(self.evidence) <= fields | {
            "canonical_key", "document_type", "issuer", "official_number", "title",
        }:
            raise ValueError("unknown metadata evidence field")
        if any(getattr(self, name) is not None and not self.evidence.get(name, "").strip()
               for name in fields):
            raise ValueError("known dates require evidence locators")
        if self.effective_to is not None and (
            self.effective_from is None or self.effective_to <= self.effective_from
        ):
            raise ValueError("invalid effective date interval")
        return self


class MaterialPreparationInput(ContractModel):
    """A preparation revision cannot set approval or trust state."""

    raw_sha256: Digest
    title: str = Field(min_length=1, max_length=1000)
    kind: MaterialKind
    group_key: MaterialGroup
    parser_version: str = Field(min_length=1, max_length=80)
    source_url: str | None = Field(
        default=None,
        pattern=r"^https://internet\.garant\.ru/document/redirect/[0-9]{1,20}/0$",
    )
    source_locator: str | None = Field(default=None, min_length=3, max_length=1000)
    reference_year: int | None = Field(default=None, ge=1900, le=2200, strict=True)
    reference_year_locator: str | None = Field(default=None, min_length=3, max_length=1000)
    parts: list[PreparedPart] = Field(default_factory=list, max_length=20)
    extraction_scope: Literal["NONE", "PARTIAL", "FULL_DOCUMENT"]
    normalized_text: str = Field(default="", max_length=25_000_000)
    normalized_sha256: Digest | None = None
    completeness_locator: str | None = Field(default=None, min_length=3, max_length=1000)
    limitations: list[EvidenceLocator] = Field(default_factory=list, max_length=40)

    @field_validator("title", "parser_version")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("blank or invalid preparation field")
        return value.strip()

    @model_validator(mode="after")
    def consistent_scope(self) -> "MaterialPreparationInput":
        if (self.kind == "CLINICAL_REFERENCE") != (self.group_key == "clinical"):
            raise ValueError("clinical references require their own group")
        if self.kind == "REFERENCE_FORM" and self.group_key != "healthcare":
            raise ValueError("reference forms belong in healthcare")
        if self.kind != "NORMATIVE" and self.parts:
            raise ValueError("references cannot have normative parts")
        if len({part.part_key for part in self.parts}) != len(self.parts):
            raise ValueError("duplicate document parts")
        if self.reference_year is not None and (
            self.kind == "NORMATIVE" or not self.reference_year_locator
        ):
            raise ValueError("reference year needs cover provenance")
        if self.source_url is not None and not self.source_locator:
            raise ValueError("source URL needs heading provenance")
        if "\x00" in self.normalized_text:
            raise ValueError("invalid extracted text")
        if self.extraction_scope == "NONE":
            if self.normalized_text or self.normalized_sha256 is not None:
                raise ValueError("unextracted material cannot contain extracted text")
        elif not self.normalized_text.strip() or self.normalized_sha256 != hashlib.sha256(
            self.normalized_text.encode()
        ).hexdigest():
            raise ValueError("extracted text hash mismatch")
        if self.extraction_scope == "PARTIAL" and not self.limitations:
            raise ValueError("partial extraction must disclose limitations")
        if self.extraction_scope == "FULL_DOCUMENT" and (
            self.limitations or not self.completeness_locator
        ):
            raise ValueError("full extraction requires completeness evidence")
        return self

    def metadata(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"normalized_text"})

    def digest(self) -> str:
        # normalized_sha256 binds the separately stored, potentially large text.
        return hashlib.sha256(json.dumps(
            self.metadata(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
