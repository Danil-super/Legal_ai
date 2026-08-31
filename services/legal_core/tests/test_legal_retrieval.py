import asyncio
import os
from datetime import date
from pathlib import Path

import pytest
from legal_core.corpus_loader import ingest_manifest
from legal_core.database import database_url
from legal_core.embedding_indexer import index_approved_fragments
from legal_core.legal_approval import ApprovalAttestation, approve_legal_version
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository
from legal_core.models import LegalVersion, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).parents[3]
MANIFEST = ROOT / "services/legal_core/tests/fixtures/official_test_manifest.json"


class FakeEmbeddingProvider:
    model_key = "integration:test-embedding:v1"
    dimensions = 3

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0, 0.0) for _ in texts)


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL retrieval tests",
)
def test_retrieval_excludes_pending_content_and_enforces_effective_dates() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        version_id = await ingest_manifest(factory, MANIFEST)
        try:
            async with factory() as session:
                transaction = await session.begin()
                try:
                    repository = ApprovedLegalCorpusRepository(session)
                    pending = await repository.search(
                        "медицинских услуг", as_of_date=date(2026, 8, 22)
                    )
                    assert pending == []

                    reviewer = await session.scalar(
                        select(User).where(User.telegram_user_id == 8_220_260_001)
                    )
                    if reviewer is None:
                        reviewer = User(
                            telegram_user_id=8_220_260_001,
                            display_name="Integration legal reviewer",
                            system_role="LEGAL_EDITOR",
                        )
                        session.add(reviewer)
                    await session.flush()
                    version = await session.get(LegalVersion, version_id)
                    assert version is not None

                    await transaction.commit()
                    await approve_legal_version(
                        factory,
                        ApprovalAttestation(
                            reviewer_telegram_user_id=reviewer.telegram_user_id,
                            version_id=version.id,
                            expected_sha256=version.raw_sha256,
                            expected_normalized_sha256=version.normalized_sha256,
                            expected_fragments_sha256=version.fragments_sha256,
                            expected_effective_from=version.effective_from,
                            expected_effective_to=version.effective_to,
                            source_is_official=True,
                            artifact_is_complete=True,
                            effective_dates_verified=True,
                            fragments_verified=True,
                        ),
                    )

                    await session.begin()

                    current = await repository.search(
                        "медицинских услуг", as_of_date=date(2026, 8, 22)
                    )
                    expired = await repository.search(
                        "медицинских услуг", as_of_date=date(2026, 9, 1)
                    )

                    assert current
                    assert all(
                        item.source_url.startswith("https://government.ru/")
                        for item in current
                    )
                    assert expired == []
                finally:
                    if session.in_transaction():
                        await session.rollback()

            stats = await index_approved_fragments(
                factory,
                provider=FakeEmbeddingProvider(),
                batch_size=8,
                max_fragments=50,
            )
            assert stats.indexed_fragments > 0
            assert stats.model_key == FakeEmbeddingProvider.model_key

            async with factory() as session:
                semantic_repository = ApprovedLegalCorpusRepository(
                    session,
                    embedding_provider=FakeEmbeddingProvider(),
                )
                # Deliberately no lexical match: the result must come from the approved-only
                # pgvector branch, while the effective-date guard remains identical.
                semantic = await semantic_repository.search(
                    "абсолютно иной безопасный тестовый запрос",
                    as_of_date=date(2026, 8, 22),
                    semantic=True,
                )
                semantic_expired = await semantic_repository.search(
                    "абсолютно иной безопасный тестовый запрос",
                    as_of_date=date(2026, 9, 1),
                    semantic=True,
                )
                assert semantic
                assert all(item.source_url.startswith("https://government.ru/") for item in semantic)
                assert semantic_expired == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())
