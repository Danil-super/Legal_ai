"""Approved-only, effective-date-aware hybrid access to the production legal corpus."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.embedding_provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    embedding_provider_from_environment,
)
from legal_core.pseudonymization import contains_obvious_direct_identifier, pseudonymize_text

logger = logging.getLogger(__name__)
_RRF_K = 60.0


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


_FRAGMENT_COLUMNS = """
    fragment_id, version_id, document_id, article, part, point,
    structural_path, fragment_text, text_sha256, effective_from,
    effective_to, source_url, raw_sha256, document_title, issuer,
    official_number, version_date, publication_date
"""

_VECTOR_FRAGMENT_COLUMNS = """
    p.fragment_id AS fragment_id,
    p.version_id AS version_id,
    p.document_id AS document_id,
    p.article AS article,
    p.part AS part,
    p.point AS point,
    p.structural_path AS structural_path,
    p.fragment_text AS fragment_text,
    p.text_sha256 AS text_sha256,
    p.effective_from AS effective_from,
    p.effective_to AS effective_to,
    p.source_url AS source_url,
    p.raw_sha256 AS raw_sha256,
    p.document_title AS document_title,
    p.issuer AS issuer,
    p.official_number AS official_number,
    p.version_date AS version_date,
    p.publication_date AS publication_date
"""

_SEARCH_APPROVED_LEXICAL = text(
    f"""
    SELECT {_FRAGMENT_COLUMNS}
      FROM production_legal_fragments
     WHERE effective_from <= :as_of_date
       AND (effective_to IS NULL OR :as_of_date < effective_to)
       AND (
            strpos(lower(fragment_text), lower(:query)) > 0
            OR to_tsvector('russian', fragment_text)
               @@ plainto_tsquery('russian', :query)
       )
     ORDER BY CASE
                  WHEN strpos(lower(fragment_text), lower(:query)) > 0 THEN 1
                  ELSE 0
              END DESC,
              ts_rank_cd(
                  to_tsvector('russian', fragment_text),
                  plainto_tsquery('russian', :query)
              ) DESC,
              document_id,
              fragment_id
     LIMIT :limit
    """
)

_SEARCH_APPROVED_VECTOR = text(
    f"""
    SELECT {_VECTOR_FRAGMENT_COLUMNS}
      FROM production_legal_fragments AS p
      JOIN legal_fragment_embeddings AS e
        ON e.fragment_id = p.fragment_id
       AND e.fragment_text_sha256 = p.text_sha256
     WHERE p.effective_from <= :as_of_date
       AND (p.effective_to IS NULL OR :as_of_date < p.effective_to)
       AND e.model_key = :model_key
       AND e.dimensions = :dimensions
     ORDER BY e.embedding <=> CAST(:embedding AS vector),
              p.document_id,
              p.fragment_id
     LIMIT :limit
    """
)


def _row_fragment(row: RowMapping) -> ApprovedLegalFragment:
    return ApprovedLegalFragment(**dict(row))


def _vector_literal(values: Sequence[float], *, expected_dimensions: int) -> str:
    if len(values) != expected_dimensions:
        raise ValueError("query embedding has an unexpected vector size")
    rendered: list[str] = []
    for item in values:
        value = float(item)
        if not math.isfinite(value) or abs(value) > 1_000_000:
            raise ValueError("query embedding contains an invalid numeric value")
        rendered.append(format(value, ".12g"))
    return "[" + ",".join(rendered) + "]"


def _rrf_merge(
    lexical: Sequence[ApprovedLegalFragment],
    semantic: Sequence[ApprovedLegalFragment],
    *,
    limit: int,
) -> list[ApprovedLegalFragment]:
    scores: dict[UUID, float] = {}
    fragments: dict[UUID, ApprovedLegalFragment] = {}
    for ranked in (lexical, semantic):
        for rank, fragment in enumerate(ranked, start=1):
            fragments.setdefault(fragment.fragment_id, fragment)
            scores[fragment.fragment_id] = scores.get(fragment.fragment_id, 0.0) + (
                1.0 / (_RRF_K + rank)
            )
    ordered = sorted(
        fragments.values(),
        key=lambda item: (
            -scores[item.fragment_id],
            str(item.document_id),
            str(item.fragment_id),
        ),
    )
    return ordered[:limit]


class ApprovedLegalCorpusRepository:
    """Runtime repository that cannot read draft or review-required records.

    Local exact/FTS search is always available. Semantic search is opt-in per query and only runs
    when a configured embedding provider exists. Provider failures degrade to lexical retrieval;
    they never widen the corpus or bypass approval/effective-date filters.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._session = session
        self._embedding_provider = embedding_provider
        self._embedding_provider_loaded = embedding_provider is not None

    async def _lexical(
        self,
        query: str,
        *,
        as_of_date: date,
        limit: int,
    ) -> list[ApprovedLegalFragment]:
        result = await self._session.execute(
            _SEARCH_APPROVED_LEXICAL,
            {"query": query, "as_of_date": as_of_date, "limit": limit},
        )
        return [_row_fragment(row) for row in result.mappings()]

    async def _semantic(
        self,
        embedding: Sequence[float],
        *,
        provider: EmbeddingProvider,
        as_of_date: date,
        limit: int,
    ) -> list[ApprovedLegalFragment]:
        result = await self._session.execute(
            _SEARCH_APPROVED_VECTOR,
            {
                "embedding": _vector_literal(
                    embedding,
                    expected_dimensions=provider.dimensions,
                ),
                "model_key": provider.model_key,
                "dimensions": provider.dimensions,
                "as_of_date": as_of_date,
                "limit": limit,
            },
        )
        return [_row_fragment(row) for row in result.mappings()]

    def _provider(self) -> EmbeddingProvider | None:
        if not self._embedding_provider_loaded:
            self._embedding_provider = embedding_provider_from_environment()
            self._embedding_provider_loaded = True
        return self._embedding_provider

    async def search(
        self,
        query: str,
        *,
        as_of_date: date,
        limit: int = 10,
        semantic: bool = False,
    ) -> list[ApprovedLegalFragment]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("legal search query must not be blank")
        if len(normalized_query) > 500:
            raise ValueError("legal search query must not exceed 500 characters")
        if not 1 <= limit <= 20:
            raise ValueError("legal search limit must be between 1 and 20")

        candidate_limit = min(60, max(limit, limit * 3))
        lexical = await self._lexical(
            normalized_query,
            as_of_date=as_of_date,
            limit=candidate_limit,
        )
        if not semantic:
            return lexical[:limit]

        provider = self._provider()
        if provider is None:
            return lexical[:limit]

        # This is a second local guard. Callers are still expected to mark only deterministic,
        # non-patient legal queries as semantic-safe.
        safe_query = pseudonymize_text(normalized_query).text
        if contains_obvious_direct_identifier(safe_query):
            logger.warning("semantic legal retrieval skipped because redaction guard failed")
            return lexical[:limit]
        try:
            vectors = await provider.embed((safe_query,))
            semantic_hits = await self._semantic(
                vectors[0],
                provider=provider,
                as_of_date=as_of_date,
                limit=candidate_limit,
            )
        except EmbeddingProviderError as exc:
            logger.warning(
                "semantic legal retrieval unavailable for model %s: %s",
                provider.model_key,
                type(exc).__name__,
            )
            return lexical[:limit]
        return _rrf_merge(lexical, semantic_hits, limit=limit)
