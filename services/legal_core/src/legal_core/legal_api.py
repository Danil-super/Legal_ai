"""Authenticated, read-only API for approved legal evidence fragments."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.api_contracts import LegalFragmentResponse, LegalFragmentSearchResponse
from legal_core.case_api import TelegramUserId, resolve_actor
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

    return router
