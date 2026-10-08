"""Unapproved, checksum-bound preparation of privately supplied originals."""

import hashlib
import json
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.contracts import ContractModel
from legal_core.models import LegalMaterialPreparation, LegalReviewMaterial

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
    date_basis: Literal["DATED_EDITION", "LAWYER_CURRENT_COPY"] = "DATED_EDITION"
    copy_valid_from: date | None = None
    text_start: int | None = Field(default=None, ge=0, strict=True)
    text_end: int | None = Field(default=None, gt=0, strict=True)
    text_sha256: Digest | None = None
    evidence: dict[str, EvidenceLocator] = Field(default_factory=dict, max_length=16)

    def missing_fields(self) -> list[str]:
        return [
            name
            for name in (
                "canonical_key",
                "document_type",
                "issuer",
                "official_number",
                "adoption_date",
                "publication_date",
                "version_date",
                "effective_from",
            )
            if getattr(self, name) is None
        ]

    @model_validator(mode="after")
    def dates_have_provenance(self) -> "PreparedPart":
        if self.date_basis == "LAWYER_CURRENT_COPY":
            if self.copy_valid_from is None or not self.evidence.get("copy_valid_from"):
                raise ValueError("current copies require applicability review provenance")
            if self.effective_from is not None:
                raise ValueError("current-copy review date is not a statutory effective date")
        elif self.copy_valid_from is not None:
            raise ValueError("dated editions cannot declare current-copy applicability")
        fields = {
            "adoption_date",
            "publication_date",
            "version_date",
            "effective_from",
            "effective_to",
        }
        if not set(self.evidence) <= fields | {
            "canonical_key",
            "document_type",
            "issuer",
            "official_number",
            "title",
            "copy_valid_from",
        }:
            raise ValueError("unknown metadata evidence field")
        if any(
            getattr(self, name) is not None and not self.evidence.get(name, "").strip()
            for name in fields
        ):
            raise ValueError("known dates require evidence locators")
        applicability_start = self.effective_from or self.copy_valid_from
        if self.effective_to is not None and (
            applicability_start is None or self.effective_to <= applicability_start
        ):
            raise ValueError("invalid effective date interval")
        scope = (self.text_start, self.text_end, self.text_sha256)
        if any(item is not None for item in scope) and (
            any(item is None for item in scope)
            or self.text_end is None
            or self.text_start is None
            or self.text_end <= self.text_start
        ):
            raise ValueError("part text scope must be complete and nonempty")
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

    @field_validator("completeness_locator")
    @classmethod
    def nonblank_completeness_locator(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("blank or invalid completeness evidence")
        return value

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
        elif (
            not self.normalized_text.strip()
            or self.normalized_sha256 != hashlib.sha256(self.normalized_text.encode()).hexdigest()
        ):
            raise ValueError("extracted text hash mismatch")
        if self.extraction_scope == "PARTIAL" and not self.limitations:
            raise ValueError("partial extraction must disclose limitations")
        if self.extraction_scope == "FULL_DOCUMENT" and (
            self.limitations or not self.completeness_locator
        ):
            raise ValueError("full extraction requires completeness evidence")
        scoped = [part for part in self.parts if part.text_start is not None]
        if scoped:
            current_copy = all(part.date_basis == "LAWYER_CURRENT_COPY" for part in self.parts)
            if (
                self.kind != "NORMATIVE"
                or (
                    self.extraction_scope != "FULL_DOCUMENT"
                    and not (current_copy and self.extraction_scope == "PARTIAL")
                )
                or (len(scoped) != len(self.parts))
            ):
                raise ValueError("all normative parts require complete extraction scopes")
            cursor = 0
            for part in self.parts:
                if part.text_start != cursor or part.text_end is None:
                    raise ValueError("part scopes must be ordered and contiguous")
                section = self.normalized_text[cursor : part.text_end]
                if not section or hashlib.sha256(section.encode()).hexdigest() != part.text_sha256:
                    raise ValueError("part scoped text hash mismatch")
                cursor = part.text_end
            if cursor != len(self.normalized_text):
                raise ValueError("part scopes must cover the full normalized text")
        return self

    def metadata(self) -> dict[str, object]:
        payload = self.model_dump(mode="json", exclude={"normalized_text"})
        # Existing immutable metadata digests must not change when defaults are added.
        for part in payload["parts"]:
            if part["date_basis"] == "DATED_EDITION":
                part.pop("date_basis")
                part.pop("copy_valid_from")
        return payload

    def digest(self) -> str:
        # normalized_sha256 binds the separately stored, potentially large text.
        return hashlib.sha256(
            json.dumps(
                self.metadata(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()


async def store_preparation(
    session: AsyncSession,
    material_id: UUID,
    payload: MaterialPreparationInput,
) -> LegalMaterialPreparation:
    """Append in a caller-owned transaction; replay never replaces a newer revision."""
    original = (
        await session.execute(
            select(LegalReviewMaterial.raw_sha256, LegalReviewMaterial.kind)
            .where(LegalReviewMaterial.id == material_id)
            .with_for_update()
        )
    ).one_or_none()
    if original is None or original.raw_sha256 != payload.raw_sha256:
        raise ValueError("preparation does not match the original artifact")
    if (original.kind == "CLINICAL_REFERENCE") != (payload.kind == "CLINICAL_REFERENCE"):
        raise ValueError("preparation kind conflicts with original artifact")
    digest = payload.digest()
    existing = await session.scalar(
        select(LegalMaterialPreparation).where(
            LegalMaterialPreparation.material_id == material_id,
            LegalMaterialPreparation.preparation_sha256 == digest,
        )
    )
    if existing is not None:
        return existing
    revision = await session.scalar(
        select(func.max(LegalMaterialPreparation.revision)).where(
            LegalMaterialPreparation.material_id == material_id,
        )
    )
    prepared = LegalMaterialPreparation(
        material_id=material_id,
        raw_sha256=payload.raw_sha256,
        revision=(revision or 0) + 1,
        preparation_sha256=digest,
        title=payload.title,
        kind=payload.kind,
        group_key=payload.group_key,
        metadata_json=payload.metadata(),
        normalized_text=payload.normalized_text,
    )
    session.add(prepared)
    await session.flush()
    return prepared
