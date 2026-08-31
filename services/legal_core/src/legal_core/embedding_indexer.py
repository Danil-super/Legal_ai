"""Idempotently build pgvector embeddings for APPROVED production legal fragments.

Only public legal text is sent to the configured embedding endpoint.  The job cannot approve legal
versions and never modifies an existing embedding: changing model/provider behaviour requires a new
versioned ``model_key``.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.database import create_engine, create_session_factory
from legal_core.embedding_provider import EmbeddingProvider, embedding_provider_from_environment

_SELECT_MISSING = text(
    """
    SELECT p.fragment_id,
           p.fragment_text,
           p.text_sha256,
           e.fragment_text_sha256 AS embedded_text_sha256
      FROM production_legal_fragments AS p
      LEFT JOIN legal_fragment_embeddings AS e
        ON e.fragment_id = p.fragment_id
       AND e.model_key = :model_key
     WHERE e.fragment_id IS NULL
        OR e.fragment_text_sha256 <> p.text_sha256
     ORDER BY p.document_id, p.version_id, p.fragment_id
     LIMIT :limit
    """
)

_INSERT_EMBEDDING = text(
    """
    INSERT INTO legal_fragment_embeddings (
        fragment_id,
        model_key,
        dimensions,
        embedding,
        fragment_text_sha256
    )
    VALUES (
        :fragment_id,
        :model_key,
        :dimensions,
        CAST(:embedding AS vector),
        :fragment_text_sha256
    )
    ON CONFLICT (fragment_id, model_key) DO NOTHING
    """
)


@dataclass(frozen=True, slots=True)
class EmbeddingIndexStats:
    indexed_fragments: int
    model_key: str
    dimensions: int


def vector_literal(values: Sequence[float], *, expected_dimensions: int) -> str:
    if len(values) != expected_dimensions:
        raise ValueError("embedding vector has an unexpected size")
    rendered: list[str] = []
    for item in values:
        value = float(item)
        if not math.isfinite(value) or abs(value) > 1_000_000:
            raise ValueError("embedding vector contains an invalid numeric value")
        rendered.append(format(value, ".12g"))
    return "[" + ",".join(rendered) + "]"


async def _next_batch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider: EmbeddingProvider,
    limit: int,
) -> list[dict[str, object]]:
    async with session_factory() as session:
        result = await session.execute(
            _SELECT_MISSING,
            {"model_key": provider.model_key, "limit": limit},
        )
        return [dict(row) for row in result.mappings()]


async def index_approved_fragments(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider: EmbeddingProvider,
    batch_size: int = 16,
    max_fragments: int = 2_000,
) -> EmbeddingIndexStats:
    if not 1 <= batch_size <= 32:
        raise ValueError("embedding batch size must be between 1 and 32")
    if not 1 <= max_fragments <= 100_000:
        raise ValueError("max_fragments must be between 1 and 100000")

    indexed = 0
    while indexed < max_fragments:
        batch = await _next_batch(
            session_factory,
            provider=provider,
            limit=min(batch_size, max_fragments - indexed),
        )
        if not batch:
            break
        stale = [
            row
            for row in batch
            if row.get("embedded_text_sha256") is not None
            and row.get("embedded_text_sha256") != row.get("text_sha256")
        ]
        if stale:
            raise RuntimeError(
                "an existing embedding no longer matches immutable fragment text; "
                "use a new model_key after investigation"
            )

        texts = [row.get("fragment_text") for row in batch]
        if not all(isinstance(value, str) and value.strip() for value in texts):
            raise RuntimeError("production legal fragment text is invalid")
        vectors = await provider.embed(tuple(value for value in texts if isinstance(value, str)))
        if len(vectors) != len(batch):
            raise RuntimeError("embedding provider returned an incomplete batch")

        async with session_factory() as session, session.begin():
            for row, vector in zip(batch, vectors, strict=True):
                fragment_id = row.get("fragment_id")
                text_sha256 = row.get("text_sha256")
                if fragment_id is None or not isinstance(text_sha256, str):
                    raise RuntimeError("production legal fragment identity is invalid")
                await session.execute(
                    _INSERT_EMBEDDING,
                    {
                        "fragment_id": fragment_id,
                        "model_key": provider.model_key,
                        "dimensions": provider.dimensions,
                        "embedding": vector_literal(
                            vector,
                            expected_dimensions=provider.dimensions,
                        ),
                        "fragment_text_sha256": text_sha256,
                    },
                )
        indexed += len(batch)

    return EmbeddingIndexStats(
        indexed_fragments=indexed,
        model_key=provider.model_key,
        dimensions=provider.dimensions,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build immutable semantic embeddings for approved legal fragments"
    )
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-fragments", type=int, default=2_000)
    return parser


async def _run_cli() -> None:
    args = _parser().parse_args()
    provider = embedding_provider_from_environment()
    if provider is None:
        raise RuntimeError(
            "semantic retrieval is not configured; set LEGAL_EMBEDDING_BASE_URL, MODEL, "
            "MODEL_KEY and DIMENSIONS"
        )
    engine = create_engine()
    factory = create_session_factory(engine)
    try:
        stats = await index_approved_fragments(
            factory,
            provider=provider,
            batch_size=args.batch_size,
            max_fragments=args.max_fragments,
        )
        print(
            f"indexed {stats.indexed_fragments} legal fragments "
            f"with {stats.model_key} ({stats.dimensions}d)"
        )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run_cli())


if __name__ == "__main__":
    main()
