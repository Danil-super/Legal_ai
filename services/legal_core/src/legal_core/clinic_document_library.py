"""Read-only tenant clinic document library status for operators.

This endpoint intentionally exposes metadata and review state only. Raw bytes and normalized text
remain behind the existing ingestion/retrieval boundaries.
"""

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.case_api import TelegramUserId, resolve_actor
from legal_core.contracts import ContractModel


class ClinicDocumentLibraryVersion(ContractModel):
    id: UUID
    version_no: int = Field(alias="versionNo", ge=1)
    source_filename: str = Field(alias="sourceFilename")
    mime_type: str = Field(alias="mimeType")
    raw_sha256: str = Field(alias="rawSha256", pattern=r"^[0-9a-f]{64}$")
    normalized_text_sha256: str = Field(
        alias="normalizedTextSha256", pattern=r"^[0-9a-f]{64}$"
    )
    valid_from: date | None = Field(alias="validFrom")
    valid_to: date | None = Field(alias="validTo")
    review_state: str = Field(alias="reviewState")
    review_reason_code: str | None = Field(alias="reviewReasonCode")
    reviewed_at: datetime | None = Field(alias="reviewedAt")
    created_at: datetime = Field(alias="createdAt")


class ClinicDocumentLibraryItem(ContractModel):
    id: UUID
    document_key: str = Field(alias="documentKey")
    document_type: str = Field(alias="documentType")
    title: str
    created_at: datetime = Field(alias="createdAt")
    versions: list[ClinicDocumentLibraryVersion]


class ClinicDocumentLibraryResponse(ContractModel):
    items: list[ClinicDocumentLibraryItem]


def create_clinic_document_library_router(
    session_factory: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(prefix="/v1/clinic-document-library", tags=["clinic-documents"])

    async def get_session() -> Any:
        async with session_factory() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(get_session)]

    @router.get("", response_model=ClinicDocumentLibraryResponse)
    async def library(
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ClinicDocumentLibraryResponse:
        actor = await resolve_actor(session, telegram_user_id)
        rows = (
            await session.execute(
                text(
                    "SELECT d.id AS document_id, d.document_key, d.document_type, d.title, "
                    "d.created_at AS document_created_at, v.id AS version_id, v.version_no, "
                    "v.source_filename, v.mime_type, v.raw_sha256, v.normalized_text_sha256, "
                    "v.valid_from, v.valid_to, v.created_at AS version_created_at, "
                    "review.decision AS review_state, review.reason_code AS review_reason_code, "
                    "review.created_at AS reviewed_at "
                    "FROM clinic_documents AS d "
                    "LEFT JOIN clinic_document_versions AS v "
                    "ON v.clinic_id=d.clinic_id AND v.document_id=d.id "
                    "LEFT JOIN LATERAL ("
                    "SELECT e.decision, e.reason_code, e.created_at "
                    "FROM clinic_document_approval_events AS e "
                    "WHERE e.clinic_id=v.clinic_id AND e.version_id=v.id "
                    "ORDER BY e.created_at DESC, e.id DESC LIMIT 1"
                    ") AS review ON true "
                    "WHERE d.clinic_id=:clinic_id "
                    "ORDER BY d.document_type, d.document_key, v.version_no DESC, v.id"
                ),
                {"clinic_id": actor.clinic_id},
            )
        ).mappings().all()

        documents: dict[UUID, ClinicDocumentLibraryItem] = {}
        for row in rows:
            document_id = row["document_id"]
            item = documents.get(document_id)
            if item is None:
                item = ClinicDocumentLibraryItem(
                    id=document_id,
                    documentKey=row["document_key"],
                    documentType=row["document_type"],
                    title=row["title"],
                    createdAt=row["document_created_at"],
                    versions=[],
                )
                documents[document_id] = item
            if row["version_id"] is None:
                continue
            item.versions.append(
                ClinicDocumentLibraryVersion(
                    id=row["version_id"],
                    versionNo=row["version_no"],
                    sourceFilename=row["source_filename"],
                    mimeType=row["mime_type"],
                    rawSha256=row["raw_sha256"],
                    normalizedTextSha256=row["normalized_text_sha256"],
                    validFrom=row["valid_from"],
                    validTo=row["valid_to"],
                    reviewState=row["review_state"] or "PENDING",
                    reviewReasonCode=row["review_reason_code"],
                    reviewedAt=row["reviewed_at"],
                    createdAt=row["version_created_at"],
                )
            )
        return ClinicDocumentLibraryResponse(items=list(documents.values()))

    return router
