import asyncio
import os
from pathlib import Path

import pytest
from legal_core.corpus_loader import ingest_manifest, load_manifest
from legal_core.database import database_url
from legal_core.models import LegalFragment, LegalSource, LegalVersion
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).parents[3]
MANIFEST = ROOT / "services/legal_core/corpus/initial_pp736.json"


def test_initial_manifest_is_official_checksum_locked_and_not_auto_approved() -> None:
    manifest = load_manifest(MANIFEST)

    assert manifest.source_url.startswith("https://government.ru/")
    assert manifest.allowed_hosts == ["government.ru"]
    assert manifest.effective_from.isoformat() == "2023-09-01"
    assert manifest.effective_to is not None
    assert manifest.effective_to.isoformat() == "2026-09-01"
    assert manifest.approval_state == "REVIEW_REQUIRED"
    assert len(manifest.fragments) >= 4


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL corpus tests",
)
def test_manifest_ingestion_is_idempotent_and_stays_review_required() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            first = await ingest_manifest(factory, MANIFEST)
            second = await ingest_manifest(factory, MANIFEST)
            assert first == second
            async with factory() as session:
                version = await session.get(LegalVersion, first)
                assert version is not None
                assert version.approval_state == "REVIEW_REQUIRED"
                assert version.regression_passed is False
                fragment_count = await session.scalar(
                    select(func.count(LegalFragment.id)).where(LegalFragment.version_id == first)
                )
                assert fragment_count == 4
                source = await session.scalar(
                    select(LegalSource).where(LegalSource.source_key == "government-ru")
                )
                assert source is not None
                assert source.status == "DRAFT"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
