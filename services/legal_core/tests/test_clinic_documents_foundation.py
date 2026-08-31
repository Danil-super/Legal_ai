import asyncio
import hashlib
import os
from uuid import uuid4

import pytest
from legal_core.database import database_url
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run clinic document integration tests",
)


def test_clinic_documents_require_same_tenant_approval_before_retrieval() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        clinic_a = uuid4()
        clinic_b = uuid4()
        user_a = uuid4()
        membership_a = uuid4()
        document_id = uuid4()
        version_id = uuid4()
        fragment_id = uuid4()
        raw_sha = hashlib.sha256(b"synthetic clinic document").hexdigest()
        normalized_text = "Гарантийное положение тестовой клиники."
        normalized_sha = hashlib.sha256(normalized_text.encode()).hexdigest()
        fragment_text = "Гарантийный срок определяется документами конкретной клиники."
        fragment_sha = hashlib.sha256(fragment_text.encode()).hexdigest()

        try:
            async with factory() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO clinics (id, name) "
                        "VALUES (:a, 'Clinic A'), (:b, 'Clinic B')"
                    ),
                    {"a": clinic_a, "b": clinic_b},
                )
                await session.execute(
                    text(
                        "INSERT INTO users (id, telegram_user_id, status) "
                        "VALUES (:id, :telegram_id, 'ACTIVE')"
                    ),
                    {"id": user_a, "telegram_id": 9_100_000_001},
                )
                await session.execute(
                    text(
                        "INSERT INTO clinic_users "
                        "(id, clinic_id, user_id, role, status) "
                        "VALUES (:id, :clinic_id, :user_id, 'CLINIC_ADMIN', 'ACTIVE')"
                    ),
                    {"id": membership_a, "clinic_id": clinic_a, "user_id": user_a},
                )
                await session.execute(
                    text("SELECT set_config('app.current_clinic_id', :clinic_id, true)"),
                    {"clinic_id": str(clinic_a)},
                )
                await session.execute(
                    text(
                        "INSERT INTO clinic_documents "
                        "(id, clinic_id, document_key, document_type, title, "
                        "created_by_membership_id) VALUES "
                        "(:id, :clinic_id, 'warranty-main', 'WARRANTY_POLICY', "
                        "'Гарантийное положение', :membership_id)"
                    ),
                    {
                        "id": document_id,
                        "clinic_id": clinic_a,
                        "membership_id": membership_a,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO clinic_document_versions "
                        "(id, clinic_id, document_id, version_no, source_filename, mime_type, "
                        "raw_object_key, raw_sha256, normalized_text, normalized_text_sha256, "
                        "created_by_membership_id) VALUES "
                        "(:id, :clinic_id, :document_id, 1, 'warranty.pdf', 'application/pdf', "
                        "'clinic-a/warranty/v1.pdf', :raw_sha, :normalized_text, :normalized_sha, "
                        ":membership_id)"
                    ),
                    {
                        "id": version_id,
                        "clinic_id": clinic_a,
                        "document_id": document_id,
                        "raw_sha": raw_sha,
                        "normalized_text": normalized_text,
                        "normalized_sha": normalized_sha,
                        "membership_id": membership_a,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO clinic_document_fragments "
                        "(id, clinic_id, version_id, ordinal, structural_path, "
                        "fragment_text, text_sha256) VALUES "
                        "(:id, :clinic_id, :version_id, 1, 'section/1', :fragment_text, :sha)"
                    ),
                    {
                        "id": fragment_id,
                        "clinic_id": clinic_a,
                        "version_id": version_id,
                        "fragment_text": fragment_text,
                        "sha": fragment_sha,
                    },
                )
                before = (
                    await session.execute(text("SELECT * FROM approved_clinic_document_fragments"))
                ).mappings().all()
                assert before == []

                await session.execute(
                    text(
                        "INSERT INTO clinic_document_approval_events "
                        "(clinic_id, version_id, actor_membership_id, decision, reason_code, "
                        "expected_raw_sha256, expected_normalized_text_sha256) VALUES "
                        "(:clinic_id, :version_id, :membership_id, 'APPROVED', "
                        "'CLINIC_DOCUMENT_REVIEW_PASSED', :raw_sha, :normalized_sha)"
                    ),
                    {
                        "clinic_id": clinic_a,
                        "version_id": version_id,
                        "membership_id": membership_a,
                        "raw_sha": raw_sha,
                        "normalized_sha": normalized_sha,
                    },
                )
                approved = (
                    await session.execute(text("SELECT * FROM approved_clinic_document_fragments"))
                ).mappings().all()
                assert len(approved) == 1
                assert approved[0]["fragment_id"] == fragment_id
                assert approved[0]["document_type"] == "WARRANTY_POLICY"

                await session.execute(
                    text("SELECT set_config('app.current_clinic_id', :clinic_id, true)"),
                    {"clinic_id": str(clinic_b)},
                )
                other_tenant = (
                    await session.execute(text("SELECT * FROM approved_clinic_document_fragments"))
                ).mappings().all()
                assert other_tenant == []

            async with factory() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.current_clinic_id', :clinic_id, true)"),
                    {"clinic_id": str(clinic_a)},
                )
                with pytest.raises(DBAPIError, match="immutable"):
                    await session.execute(
                        text("UPDATE clinic_documents SET title='tampered' WHERE id=:id"),
                        {"id": document_id},
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
