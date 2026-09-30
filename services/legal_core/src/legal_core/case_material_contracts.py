"""Safe metadata-only contracts for temporary anonymised case materials."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from legal_core.contracts import ContractModel

CaseMaterialMimeType = Literal[
    "text/plain",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]


class CaseMaterialResponse(ContractModel):
    id: UUID
    display_name: str = Field(alias="displayName", min_length=1, max_length=120)
    mime_type: CaseMaterialMimeType = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes", ge=1, le=15_000_000)
    created_at: datetime = Field(alias="createdAt")


class CaseMaterialListResponse(ContractModel):
    items: list[CaseMaterialResponse] = Field(default_factory=list, max_length=10)


class CaseMaterialMutationResponse(ContractModel):
    """Result of a draft-owned material mutation.

    The revision is deliberately returned with the material metadata so a Telegram
    worker can continue to use optimistic draft updates after an attachment was
    added or removed.  It never contains a filename, object key, checksum, or
    extracted text.
    """

    material: CaseMaterialResponse
    draft_revision: int = Field(alias="draftRevision", ge=1)


class CaseMaterialDeleteResponse(ContractModel):
    id: UUID
    deleted: Literal[True] = True
    draft_revision: int = Field(alias="draftRevision", ge=1)
