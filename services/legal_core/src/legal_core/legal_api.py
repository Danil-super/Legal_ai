"""Authenticated, read-only API for approved legal evidence and lawyer library views."""

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.api_contracts import (
    LegalFragmentResponse,
    LegalFragmentSearchResponse,
    LegalLibraryDocumentResponse,
    LegalLibraryResponse,
    PlatformLegalReviewQueueItem,
    PlatformLegalReviewQueueResponse,
)
from legal_core.case_api import (
    ApiError,
    TelegramUserId,
    _configured_platform_owner_telegram_id,
    resolve_actor,
)
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository
from legal_core.models import LegalDocument, LegalFragment, LegalVersion


def create_legal_router(
    session_factory: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(prefix="/v1/legal", tags=["legal-evidence"])

    async def get_session() -> Any:
        async with session_factory() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(get_session)]
    SearchQuery = Annotated[str, Query(min_length=2, max_length=500)]
    AsOfDate = Annotated[date, Query(alias="as_of_date")]
    OptionalAsOfDate = Annotated[date | None, Query(alias="as_of_date")]
    SearchLimit = Annotated[int, Query(ge=1, le=20)]

    def require_lawyer_library_access(role: str) -> None:
        """Keep the administrator workspace minimal while preserving owner parity."""

        if role not in {"CLINIC_OWNER", "CLINIC_LAWYER"}:
            raise ApiError(
                status_code=403,
                code="LEGAL_LIBRARY_NOT_ALLOWED",
                message="Legal library access is limited to clinic lawyers and owners",
            )

    @router.get("/fragments", response_model=LegalFragmentSearchResponse)
    async def search_fragments(
        query: SearchQuery,
        as_of_date: AsOfDate,
        telegram_user_id: TelegramUserId,
        session: Session,
        limit: SearchLimit = 10,
    ) -> LegalFragmentSearchResponse:
        await resolve_actor(session, telegram_user_id)
        fragments = await ApprovedLegalCorpusRepository(session).search(
            query,
            as_of_date=as_of_date,
            limit=limit,
        )
        return LegalFragmentSearchResponse(
            items=[
                LegalFragmentResponse.model_validate(fragment, from_attributes=True)
                for fragment in fragments
            ]
        )

    @router.get("/library", response_model=LegalLibraryResponse)
    async def list_library_documents(
        telegram_user_id: TelegramUserId,
        session: Session,
        as_of_date: OptionalAsOfDate = None,
    ) -> LegalLibraryResponse:
        """List current approved source metadata; source drafts and raw files stay private."""

        actor = await resolve_actor(session, telegram_user_id)
        require_lawyer_library_access(actor.role)
        resolved_date = as_of_date or datetime.now(UTC).date()
        documents = await ApprovedLegalCorpusRepository(session).list_documents(
            as_of_date=resolved_date
        )
        return LegalLibraryResponse(
            asOfDate=resolved_date,
            items=[
                LegalLibraryDocumentResponse.model_validate(document, from_attributes=True)
                for document in documents
            ],
        )

    @router.get("/review-queue", response_model=PlatformLegalReviewQueueResponse)
    async def list_platform_review_queue(
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> PlatformLegalReviewQueueResponse:
        """Показывает владельцу только метаданные ручной очереди юридической проверки."""

        await resolve_actor(session, telegram_user_id)
        if telegram_user_id != _configured_platform_owner_telegram_id():
            raise ApiError(
                status_code=403,
                code="OWNER_REQUIRED",
                message="Platform owner access is required",
            )
        rows = (
            await session.execute(
                select(
                    LegalVersion,
                    LegalDocument.title,
                    LegalDocument.issuer,
                    LegalDocument.official_number,
                    func.count(LegalFragment.id).label("fragment_count"),
                )
                .join(LegalDocument, LegalDocument.id == LegalVersion.document_id)
                .outerjoin(LegalFragment, LegalFragment.version_id == LegalVersion.id)
                .group_by(LegalVersion.id, LegalDocument.id)
                .order_by(LegalVersion.received_at.desc(), LegalVersion.id.desc())
                .limit(100)
            )
        ).all()
        return PlatformLegalReviewQueueResponse(
            items=[
                PlatformLegalReviewQueueItem(
                    documentId=version.document_id,
                    versionId=version.id,
                    documentTitle=title,
                    issuer=issuer,
                    officialNumber=official_number,
                    approvalState=version.approval_state,
                    effectiveFrom=version.effective_from,
                    effectiveTo=version.effective_to,
                    sourceUrl=version.source_url,
                    rawSha256=version.raw_sha256,
                    normalizedSha256=version.normalized_sha256,
                    fragmentsSha256=version.fragments_sha256,
                    fragmentCount=fragment_count,
                )
                for version, title, issuer, official_number, fragment_count in rows
            ]
        )

    return router
