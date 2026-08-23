import asyncio
import hashlib
import os
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    normalized_text_sha256,
)
from legal_core.database import database_url
from legal_core.legal_updater import (
    LegalUpdateCandidate,
    UpdateRunStatus,
    build_review_candidate,
    queue_review_candidate,
    record_update_run,
    review_queue_payload,
    update_run_payload,
)
from legal_core.models import (
    LegalDocument,
    LegalSource,
    LegalUpdateReviewItem,
    LegalUpdateRun,
    LegalVersion,
    User,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL updater persistence tests",
)


def _fragment() -> CorpusFragment:
    return CorpusFragment(
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading=None,
        structural_path="point:1",
        text="Синтетический фрагмент обновлённой нормы.",
    )


def _candidate_version(
    *, source_id: UUID, document_id: UUID
) -> tuple[LegalVersion, LegalUpdateCandidate]:
    fragment = _fragment()
    raw_bytes = b"%PDF-1.7\nsynthetic updater candidate\n%%EOF\n"
    normalized_text = fragment.text
    candidate = build_review_candidate(
        document_key="synthetic-updater-document",
        previous=[],
        proposed=[fragment],
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        normalized_sha256=normalized_text_sha256(normalized_text),
    )
    return (
        LegalVersion(
            document_id=document_id,
            source_id=source_id,
            version_no=1,
            source_external_id="synthetic-updater-v1",
            source_url="https://example.gov.ru/synthetic-updater-v1",
            publication_date=date(2026, 8, 1),
            version_date=date(2026, 8, 1),
            effective_from=date(2026, 8, 1),
            effective_to=None,
            approval_state="REVIEW_REQUIRED",
            artifact_kind="OFFICIAL_RAW",
            raw_sha256=candidate.raw_sha256,
            raw_mime_type="application/pdf",
            raw_bytes=raw_bytes,
            raw_size_bytes=len(raw_bytes),
            artifact_retrieved_at=datetime.now(UTC),
            artifact_page_count=1,
            normalized_text=normalized_text,
            normalized_sha256=candidate.normalized_sha256,
            fragments_sha256=corpus_fragments_sha256([fragment]),
            normalization_scope="FULL_DOCUMENT",
            parser_version="synthetic-updater.v1",
            regression_passed=False,
        ),
        candidate,
    )


def test_update_queue_is_idempotent_and_immutable() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with factory() as session, session.begin():
                editor = User(
                    telegram_user_id=int(f"83{uuid4().int % 10**8:08d}"),
                    system_role="LEGAL_EDITOR",
                    display_name="Synthetic updater legal editor",
                )
                session.add(editor)
                await session.flush()
                approved_source = LegalSource(
                    source_key=f"approved-source-{suffix}",
                    revision=1,
                    display_name="Approved synthetic update source",
                    base_url="https://example.gov.ru",
                    allowed_hosts=["example.gov.ru"],
                    trust_level="PRIMARY",
                    status="APPROVED",
                    approved_by=editor.id,
                    approved_at=datetime.now(UTC),
                )
                document = LegalDocument(
                    canonical_key=f"synthetic-updater-document-{suffix}",
                    jurisdiction="RU",
                    document_type="DECREE",
                    title="Synthetic updater document",
                    issuer="Synthetic authority",
                    official_number=f"synthetic-{suffix}",
                    adoption_date=date(2026, 8, 1),
                )
                session.add_all([approved_source, document])
                await session.flush()
                version, candidate = _candidate_version(
                    source_id=approved_source.id, document_id=document.id
                )
                session.add(version)
                await session.flush()

                first = await queue_review_candidate(
                    session,
                    source_id=approved_source.id,
                    document_id=document.id,
                    previous_legal_version_id=None,
                    candidate_legal_version_id=version.id,
                    candidate=candidate,
                )
                replay = await queue_review_candidate(
                    session,
                    source_id=approved_source.id,
                    document_id=document.id,
                    previous_legal_version_id=None,
                    candidate_legal_version_id=version.id,
                    candidate=candidate,
                )
                assert first.created is True
                assert replay == first.__class__(review_item_id=first.review_item_id, created=False)

                item = await session.get(LegalUpdateReviewItem, first.review_item_id)
                assert item is not None
                item.structural_diff_sha256 = "0" * 64
                with pytest.raises(DBAPIError, match="immutable"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_database_rejects_a_review_item_from_a_draft_source() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with factory() as session:
                source = LegalSource(
                    source_key=f"draft-source-{suffix}",
                    revision=1,
                    display_name="Draft synthetic update source",
                    base_url="https://example.gov.ru",
                    allowed_hosts=["example.gov.ru"],
                    trust_level="PRIMARY",
                    status="DRAFT",
                )
                document = LegalDocument(
                    canonical_key=f"draft-updater-document-{suffix}",
                    jurisdiction="RU",
                    document_type="DECREE",
                    title="Draft synthetic updater document",
                    issuer="Synthetic authority",
                    official_number=f"draft-{suffix}",
                    adoption_date=date(2026, 8, 1),
                )
                session.add_all([source, document])
                await session.flush()
                version, candidate = _candidate_version(
                    source_id=source.id, document_id=document.id
                )
                session.add(version)
                await session.flush()
                payload = review_queue_payload(candidate)
                session.add(
                    LegalUpdateReviewItem(
                        source_id=source.id,
                        document_id=document.id,
                        previous_legal_version_id=None,
                        candidate_legal_version_id=version.id,
                        raw_sha256=payload.raw_sha256,
                        normalized_sha256=payload.normalized_sha256,
                        fragments_sha256=payload.fragments_sha256,
                        structural_diff_sha256=payload.structural_diff_sha256,
                        structural_diff_json=payload.structural_diff_json,
                        candidate_sha256=payload.candidate_sha256,
                        status=payload.status,
                    )
                )
                with pytest.raises(DBAPIError, match="source must be approved"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_update_runs_are_idempotent_and_immutable() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with factory() as session:
                source = LegalSource(
                    source_key=f"failed-run-source-{suffix}",
                    revision=1,
                    display_name="Synthetic failed-run source",
                    base_url="https://example.gov.ru",
                    allowed_hosts=["example.gov.ru"],
                    trust_level="PRIMARY",
                    status="DRAFT",
                )
                session.add(source)
                await session.flush()
                payload = update_run_payload(
                    status=UpdateRunStatus.FETCH_FAILED,
                    idempotency_sha256="c" * 64,
                    failure_code="HTTPS_TIMEOUT",
                )
                first = await record_update_run(
                    session,
                    source_id=source.id,
                    document_id=None,
                    payload=payload,
                )
                replay = await record_update_run(
                    session,
                    source_id=source.id,
                    document_id=None,
                    payload=payload,
                )
                assert first.created is True
                assert replay == first.__class__(update_run_id=first.update_run_id, created=False)

                stored = await session.get(LegalUpdateRun, first.update_run_id)
                assert stored is not None
                assert stored.failure_code == "HTTPS_TIMEOUT"
                stored.failure_code = "TAMPERED"
                with pytest.raises(DBAPIError, match="immutable"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_database_rejects_a_tampered_update_run_result_digest() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with factory() as session:
                source = LegalSource(
                    source_key=f"tampered-run-source-{suffix}",
                    revision=1,
                    display_name="Synthetic tampered-run source",
                    base_url="https://example.gov.ru",
                    allowed_hosts=["example.gov.ru"],
                    trust_level="PRIMARY",
                    status="DRAFT",
                )
                session.add(source)
                await session.flush()
                session.add(
                    LegalUpdateRun(
                        source_id=source.id,
                        document_id=None,
                        review_item_id=None,
                        idempotency_sha256="d" * 64,
                        result_sha256="0" * 64,
                        status="FETCH_FAILED",
                        failure_code="HTTPS_TIMEOUT",
                    )
                )
                with pytest.raises(DBAPIError, match="check constraint"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
