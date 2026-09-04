"""Authenticated, read-only API for approved legal evidence and lawyer library views."""

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.api_contracts import (
    LegalFragmentResponse,
    LegalFragmentSearchResponse,
    LegalLibraryDocumentResponse,
    LegalLibraryResponse,
)
from legal_core.case_api import ApiError, TelegramUserId, resolve_actor
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository


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
        as_of_date: date | None = Query(default=None, alias="as_of_date"),
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

    return router
