import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from legal_core.database import database_url
from legal_core.models import LegalReviewMaterial
from legal_core.review_materials import ingest_review_materials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run review material persistence tests",
)


def test_importing_the_same_package_twice_is_idempotent_and_never_approves_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "clinical.pdf").write_bytes(b"%PDF-1.7\nclinical\n%%EOF\n")
    (tmp_path / "law.rtf").write_bytes(
        b"{\\rtf1\\ansi https://internet.garant.ru/document/redirect/12191967/0}"
    )
    package_key = f"test-review-{uuid4().hex}"

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            first = await ingest_review_materials(
                factory,
                tmp_path,
                package_key=package_key,
                received_at=datetime(2026, 9, 25, 12, tzinfo=UTC),
            )
            second = await ingest_review_materials(
                factory,
                tmp_path,
                package_key=package_key,
                received_at=datetime(2026, 9, 26, 12, tzinfo=UTC),
            )
            assert second == first
            async with factory() as session:
                materials = list(
                    await session.scalars(
                        select(LegalReviewMaterial)
                        .where(LegalReviewMaterial.package_key == package_key)
                        .order_by(LegalReviewMaterial.original_filename)
                    )
                )
            assert len(materials) == 2
            assert {material.kind for material in materials} == {
                "LEGAL_COPY",
                "CLINICAL_REFERENCE",
            }
            assert all(material.review_state == "METADATA_REQUIRED" for material in materials)
            assert all(material.raw_bytes for material in materials)
            assert all(
                material.received_at == datetime(2026, 9, 25, 12, tzinfo=UTC)
                for material in materials
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
