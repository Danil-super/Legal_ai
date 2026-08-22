import asyncio
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from legal_core.corpus_loader import ingest_manifest
from legal_core.database import database_url
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository
from legal_core.models import LegalSource, LegalVersion, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).parents[3]
MANIFEST = ROOT / "services/legal_core/corpus/initial_pp736.json"


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

                    reviewer = User(
                        telegram_user_id=8_220_260_001,
                        display_name="Integration legal reviewer",
                        system_role="LEGAL_EDITOR",
                    )
                    session.add(reviewer)
                    await session.flush()
                    version = await session.get(LegalVersion, version_id)
                    assert version is not None
                    source = await session.get(LegalSource, version.source_id)
                    assert source is not None
                    approved_at = datetime.now(UTC)
                    source.status = "APPROVED"
                    source.approved_by = reviewer.id
                    source.approved_at = approved_at
                    version.approval_state = "APPROVED"
                    version.regression_passed = True
                    version.approved_by = reviewer.id
                    version.approved_at = approved_at
                    await session.flush()

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
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
