"""Approved-only, effective-date-aware access to the production legal corpus."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ApprovedLegalFragment:
    fragment_id: UUID
    version_id: UUID
    document_id: UUID
    article: str | None
    part: str | None
    point: str | None
    structural_path: str
    fragment_text: str
    text_sha256: str
    effective_from: date
    effective_to: date | None
    source_url: str
    raw_sha256: str
    document_title: str
    issuer: str
    official_number: str | None
    version_date: date | None
    publication_date: date | None


_SEARCH_APPROVED = text(
    """
    SELECT fragment_id, version_id, document_id, article, part, point,
           structural_path, fragment_text, text_sha256, effective_from,
           effective_to, source_url, raw_sha256, document_title, issuer,
           official_number, version_date, publication_date
      FROM production_legal_fragments
     WHERE effective_from <= :as_of_date
       AND (effective_to IS NULL OR :as_of_date < effective_to)
       AND to_tsvector('russian', fragment_text)
           @@ plainto_tsquery('russian', :query)
     ORDER BY ts_rank(
                  to_tsvector('russian', fragment_text),
                  plainto_tsquery('russian', :query)
              ) DESC,
              document_id,
              fragment_id
     LIMIT :limit
    """
)


class ApprovedLegalCorpusRepository:
    """Runtime repository that cannot read draft or review-required records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        query: str,
        *,
        as_of_date: date,
        limit: int = 10,
    ) -> list[ApprovedLegalFragment]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("legal search query must not be blank")
        if not 1 <= limit <= 20:
            raise ValueError("legal search limit must be between 1 and 20")

        result = await self._session.execute(
            _SEARCH_APPROVED,
            {
                "query": normalized_query,
                "as_of_date": as_of_date,
                "limit": limit,
            },
        )
        return [ApprovedLegalFragment(**dict(row)) for row in result.mappings()]
