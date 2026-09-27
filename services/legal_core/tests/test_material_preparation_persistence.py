import asyncio
import hashlib
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.database import database_url
from legal_core.material_preparation import MaterialPreparationInput, store_preparation
from legal_core.models import LegalMaterialPreparation, LegalReviewMaterial, LegalVersion

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL required",
)


def test_preparation_revisions_are_immutable_retry_safe_and_not_legal_versions() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        raw = b"%PDF-1.7\nSynthetic reference\n%%EOF"
        digest = hashlib.sha256(raw).hexdigest()
        try:
            async with factory() as session, session.begin():
                material = LegalReviewMaterial(
                    package_key=f"test-prep-{uuid4().hex}", original_filename="test.pdf",
                    title="Original receipt", kind="CLINICAL_REFERENCE",
                    source_name="Synthetic source", raw_mime_type="application/pdf",
                    raw_bytes=raw, raw_size_bytes=len(raw), raw_sha256=digest,
                    received_at=datetime.now(UTC),
                )
                session.add(material)
                await session.flush()
                material_id = material.id
                before = await session.scalar(select(func.count()).select_from(LegalVersion))
            payload = MaterialPreparationInput(
                raw_sha256=digest, title="Readable synthetic title",
                kind="CLINICAL_REFERENCE", group_key="clinical", parser_version="test-v1",
                extraction_scope="NONE", limitations=["No text extracted"],
            )

            async def save(candidate: MaterialPreparationInput):
                async with factory() as session, session.begin():
                    return await store_preparation(session, material_id, candidate)

            first, retry = await asyncio.gather(save(payload), save(payload))
            assert first.id == retry.id
            assert first.revision == 1
            changed = MaterialPreparationInput.model_validate(payload.model_dump() | {
                "title": "Corrected title",
            })
            second = await save(changed)
            assert second.revision == 2
            assert (await save(payload)).id == first.id
            with pytest.raises(ValueError, match="original"):
                await save(MaterialPreparationInput.model_validate(payload.model_dump() | {
                    "raw_sha256": "b" * 64,
                }))
            with pytest.raises(ValueError, match="kind"):
                await save(MaterialPreparationInput.model_validate(payload.model_dump() | {
                    "kind": "NORMATIVE", "group_key": "general",
                }))
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(
                    LegalMaterialPreparation
                ).where(LegalMaterialPreparation.material_id == material_id)) == 2
                original = await session.get(LegalReviewMaterial, material_id)
                assert original.title == "Original receipt"
                assert original.review_state == "METADATA_REQUIRED"
                after = await session.scalar(select(func.count()).select_from(LegalVersion))
                assert after == before
                for operation in ["UPDATE legal_material_preparations SET revision=99",
                                  "DELETE FROM legal_material_preparations"]:
                    with pytest.raises(DBAPIError):
                        async with session.begin_nested():
                            await session.execute(
                                text(operation + " WHERE id=:id"), {"id": first.id}
                            )
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        forged = LegalMaterialPreparation(
                            material_id=material_id, raw_sha256=digest, revision=3,
                            preparation_sha256="c" * 64, title=payload.title,
                            kind=payload.kind, group_key=payload.group_key,
                            metadata_json=payload.metadata(), normalized_text="Forged text",
                        )
                        session.add(forged)
                        await session.flush()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
