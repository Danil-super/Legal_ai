import asyncio
import hashlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    ingest_manifest,
    normalized_text_sha256,
)
from legal_core.database import database_url
from legal_core.legal_approval import (
    ApprovalAttestation,
    LegalApprovalRejected,
    approve_legal_version,
    approve_legal_version_in_session,
)
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository
from legal_core.models import (
    LegalApprovalEvent,
    LegalFragment,
    LegalSource,
    LegalVersion,
    User,
)
from legal_core.retrieval_plan import retrieve_planned_evidence
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).parents[3]
MANIFEST = ROOT / "services/legal_core/corpus/initial_pp736.json"


def test_approval_attestation_requires_all_human_checks() -> None:
    with pytest.raises(ValueError, match="all legal-review attestations"):
        ApprovalAttestation(
            reviewer_telegram_user_id=1,
            version_id=UUID("00000000-0000-0000-0000-000000000001"),
            expected_sha256="a" * 64,
            expected_normalized_sha256="b" * 64,
            expected_fragments_sha256="c" * 64,
            expected_effective_from=date(2023, 9, 1),
            expected_effective_to=date(2026, 9, 1),
            source_is_official=True,
            artifact_is_complete=False,
            effective_dates_verified=True,
            fragments_verified=True,
        )


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL approval tests",
)
def test_normalized_excerpt_cannot_be_approved_and_attempt_is_audited() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        version_id = await ingest_manifest(factory, MANIFEST)
        reviewer_telegram_id = 8_220_260_777
        try:
            async with factory() as session, session.begin():
                reviewer = await session.scalar(
                    select(User).where(User.telegram_user_id == reviewer_telegram_id)
                )
                if reviewer is None:
                    session.add(
                        User(
                            telegram_user_id=reviewer_telegram_id,
                            display_name="Approval integration reviewer",
                            system_role="LEGAL_EDITOR",
                        )
                    )

            async with factory() as session:
                reviewer = await session.scalar(
                    select(User).where(User.telegram_user_id == reviewer_telegram_id)
                )
                version = await session.get(LegalVersion, version_id)
                assert reviewer is not None
                assert version is not None
                version.approval_state = "APPROVED"
                version.regression_passed = True
                version.approved_by = reviewer.id
                version.approved_at = datetime.now(UTC)
                with pytest.raises(DBAPIError, match="current approval event"):
                    await session.flush()
                await session.rollback()

            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                attestation = ApprovalAttestation(
                    reviewer_telegram_user_id=reviewer_telegram_id,
                    version_id=version_id,
                    expected_sha256=version.raw_sha256,
                    expected_normalized_sha256=version.normalized_sha256,
                    expected_fragments_sha256=version.fragments_sha256,
                    expected_effective_from=version.effective_from,
                    expected_effective_to=version.effective_to,
                    source_is_official=True,
                    artifact_is_complete=True,
                    effective_dates_verified=True,
                    fragments_verified=True,
                )

            with pytest.raises(ValueError, match="OFFICIAL_RAW"):
                await approve_legal_version(factory, attestation)

            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                assert version.approval_state == "REVIEW_REQUIRED"
                attempts = await session.scalar(
                    select(func.count(LegalApprovalEvent.id)).where(
                        LegalApprovalEvent.legal_version_id == version_id,
                        LegalApprovalEvent.decision == "BLOCKED",
                    )
                )
                assert attempts == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL approval tests",
)
def test_editor_workspace_rejects_legacy_candidate_without_a_false_blocked_event() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        version_id = await ingest_manifest(factory, MANIFEST)
        reviewer_telegram_id = 8_220_260_779
        try:
            async with factory() as session, session.begin():
                session.add(
                    User(
                        telegram_user_id=reviewer_telegram_id,
                        display_name="Editor workspace rejection reviewer",
                        system_role="LEGAL_EDITOR",
                    )
                )

            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                reviewer = await session.scalar(
                    select(User).where(User.telegram_user_id == reviewer_telegram_id)
                )
                assert version is not None
                assert reviewer is not None
                attestation = ApprovalAttestation(
                    reviewer_telegram_user_id=reviewer_telegram_id,
                    version_id=version_id,
                    expected_sha256=version.raw_sha256,
                    expected_normalized_sha256=version.normalized_sha256,
                    expected_fragments_sha256=version.fragments_sha256,
                    expected_effective_from=version.effective_from,
                    expected_effective_to=version.effective_to,
                    source_is_official=True,
                    artifact_is_complete=True,
                    effective_dates_verified=True,
                    fragments_verified=True,
                )

            async with factory() as session:
                with pytest.raises(LegalApprovalRejected, match="ARTIFACT_NOT_OFFICIAL_RAW"):
                    await approve_legal_version_in_session(
                        session,
                        attestation,
                        require_review_required=True,
                        record_rejected_attempt=False,
                    )
                await session.rollback()

            async with factory() as session:
                attempts = await session.scalar(
                    select(func.count(LegalApprovalEvent.id)).where(
                        LegalApprovalEvent.legal_version_id == version_id,
                        LegalApprovalEvent.actor_user_id == reviewer.id,
                    )
                )
                assert attempts == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL approval tests",
)
def test_legal_editor_can_approve_checksum_bound_official_raw_artifact(tmp_path: Path) -> None:
    raw = b"%PDF-1.7\nofficial integration artifact\n%%EOF\n"
    (tmp_path / "official.pdf").write_bytes(raw)
    fragment = "Official integration fragment included in normalized text."
    normalized = f"Document heading. {fragment} End of document."
    corpus_fragment = CorpusFragment(
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading=None,
        structural_path="point:1",
        text=fragment,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "dental-legal-corpus.v2",
                "source_key": "official-approval-integration",
                "source_name": "Official approval integration source",
                "source_url": "https://example.gov.ru/document/approval-integration",
                "source_external_id": "approval-integration",
                "allowed_hosts": ["example.gov.ru"],
                "document_key": "approval-integration-document",
                "document_type": "DECREE",
                "title": "Approval integration legal document",
                "issuer": "Integration authority",
                "official_number": "integration-1",
                "adoption_date": "2026-01-01",
                "publication_date": "2026-01-02",
                "version_date": "2026-01-01",
                "effective_from": "2026-02-01",
                "effective_to": "2027-02-01",
                "approval_state": "REVIEW_REQUIRED",
                "artifact_kind": "OFFICIAL_RAW",
                "artifact_mime_type": "application/pdf",
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                "artifact_path": "official.pdf",
                "artifact_retrieved_at": "2026-08-22T00:00:00Z",
                "artifact_size_bytes": len(raw),
                "artifact_page_count": 1,
                "normalized_text": normalized,
                "normalized_sha256": normalized_text_sha256(normalized),
                "fragments_sha256": corpus_fragments_sha256([corpus_fragment]),
                "normalization_scope": "FULL_DOCUMENT",
                "parser_version": "approval-integration.v1",
                "fragments": [corpus_fragment.model_dump(mode="json")],
            }
        ),
        encoding="utf-8",
    )

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        reviewer_telegram_id = 8_220_260_778
        version_id = await ingest_manifest(factory, manifest_path)
        try:
            async with factory() as session, session.begin():
                reviewer = await session.scalar(
                    select(User).where(User.telegram_user_id == reviewer_telegram_id)
                )
                if reviewer is None:
                    session.add(
                        User(
                            telegram_user_id=reviewer_telegram_id,
                            display_name="Successful approval integration reviewer",
                            system_role="LEGAL_EDITOR",
                        )
                    )

            attestation = ApprovalAttestation(
                reviewer_telegram_user_id=reviewer_telegram_id,
                version_id=version_id,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
                expected_normalized_sha256=normalized_text_sha256(normalized),
                expected_fragments_sha256=corpus_fragments_sha256([corpus_fragment]),
                expected_effective_from=date(2026, 2, 1),
                expected_effective_to=date(2027, 2, 1),
                source_is_official=True,
                artifact_is_complete=True,
                effective_dates_verified=True,
                fragments_verified=True,
            )
            approved_id = await approve_legal_version(factory, attestation)
            retried_id = await approve_legal_version(factory, attestation)

            assert approved_id == version_id
            assert retried_id == version_id
            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                assert version.approval_state == "APPROVED"
                assert version.regression_passed is True
                source = await session.get(LegalSource, version.source_id)
                assert source is not None
                assert source.status == "APPROVED"
                decisions = list(
                    await session.scalars(
                        select(LegalApprovalEvent.decision)
                        .where(LegalApprovalEvent.legal_version_id == version_id)
                        .order_by(LegalApprovalEvent.created_at, LegalApprovalEvent.id)
                    )
                )
                assert decisions == ["APPROVED"]
                approved_event = await session.scalar(
                    select(LegalApprovalEvent).where(
                        LegalApprovalEvent.legal_version_id == version_id,
                        LegalApprovalEvent.decision == "APPROVED",
                    )
                )
                assert approved_event is not None
                database_digest = await session.scalar(
                    text(
                        "SELECT legal_regression_result_sha256(CAST(:payload AS jsonb))"
                    ),
                    {"payload": json.dumps(approved_event.regression_checks_json)},
                )
                assert approved_event.regression_result_sha256 == database_digest

            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                source = await session.get(LegalSource, version.source_id)
                assert source is not None
                source.status = "DRAFT"
                source.approved_by = None
                source.approved_at = None
                with pytest.raises(DBAPIError, match="source lifecycle transition"):
                    await session.flush()
                await session.rollback()

            mismatched = attestation.model_copy(update={"expected_sha256": "f" * 64})
            with pytest.raises(ValueError, match="EXPECTED_SHA_MISMATCH"):
                await approve_legal_version(factory, mismatched)

            async with factory() as session:
                decisions = list(
                    await session.scalars(
                        select(LegalApprovalEvent.decision)
                        .where(LegalApprovalEvent.legal_version_id == version_id)
                        .order_by(LegalApprovalEvent.created_at, LegalApprovalEvent.id)
                    )
                )
                assert decisions == ["APPROVED", "BLOCKED"]

            async with factory() as session:
                extra_text = "A fragment inserted too late must be rejected."
                session.add(
                    LegalFragment(
                        version_id=version_id,
                        ordinal=2,
                        article=None,
                        part=None,
                        point="2",
                        heading=None,
                        structural_path="point:2",
                        fragment_text=extra_text,
                        text_sha256=hashlib.sha256(extra_text.encode()).hexdigest(),
                    )
                )
                with pytest.raises(DBAPIError, match="approved legal version"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL approval tests",
)
def test_legal_editor_can_approve_a_consultant_copy_only_after_attesting_comparison(
    tmp_path: Path,
) -> None:
    raw = b"%PDF-1.7\nconsultant copy integration artifact\n%%EOF\n"
    artifact_path = tmp_path / "consultant-copy.pdf"
    artifact_path.write_bytes(raw)
    fragment_text = "Проверенный фрагмент из полной копии нормативного документа."
    normalized = f"Заголовок документа. {fragment_text} Конец документа."
    fragment = CorpusFragment(
        ordinal=1,
        article="13",
        part=None,
        point=None,
        heading="Проверка",
        structural_path="Статья 13",
        text=fragment_text,
    )
    manifest_path = tmp_path / "consultant-copy-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "dental-legal-corpus.v3",
                "source_key": "consultant-plus",
                "source_name": "КонсультантПлюс",
                "source_trust_level": "VERIFIED_COPY",
                "source_base_url": "https://www.consultant.ru/",
                "source_url": "https://www.consultant.ru/document/cons_doc_LAW_121895/",
                "source_external_id": "cons_doc_LAW_121895",
                "allowed_hosts": ["www.consultant.ru"],
                "document_key": "consultant-copy-approval-integration",
                "document_type": "FEDERAL_LAW",
                "title": "Проверочный федеральный закон",
                "issuer": "Российская Федерация",
                "official_number": "323-ФЗ",
                "adoption_date": "2011-11-21",
                "publication_date": "2011-11-21",
                "version_date": "2026-08-04",
                "effective_from": "2026-08-04",
                "effective_to": None,
                "approval_state": "REVIEW_REQUIRED",
                "artifact_kind": "THIRD_PARTY_VERIFIED_COPY",
                "artifact_mime_type": "application/pdf",
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                "artifact_path": artifact_path.name,
                "artifact_retrieved_at": "2026-09-11T12:00:00Z",
                "artifact_size_bytes": len(raw),
                "artifact_page_count": 1,
                "normalized_text": normalized,
                "normalized_sha256": normalized_text_sha256(normalized),
                "fragments_sha256": corpus_fragments_sha256([fragment]),
                "normalization_scope": "FULL_DOCUMENT",
                "parser_version": "pdftotext-nfkc.v1",
                "fragments": [fragment.model_dump(mode="json")],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        reviewer_telegram_id = 8_220_260_780
        try:
            version_id = await ingest_manifest(factory, manifest_path)
            async with factory() as session, session.begin():
                session.add(
                    User(
                        telegram_user_id=reviewer_telegram_id,
                        display_name="Consultant copy approval reviewer",
                        system_role="LEGAL_EDITOR",
                    )
                )

            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                assert version.artifact_kind == "THIRD_PARTY_VERIFIED_COPY"
                source = await session.get(LegalSource, version.source_id)
                assert source is not None
                assert source.trust_level == "VERIFIED_COPY"
                repository = ApprovedLegalCorpusRepository(session)
                assert not await repository.search(fragment_text, as_of_date=date(2026, 9, 12))

            attestation = ApprovalAttestation(
                reviewer_telegram_user_id=reviewer_telegram_id,
                version_id=version_id,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
                expected_normalized_sha256=normalized_text_sha256(normalized),
                expected_fragments_sha256=corpus_fragments_sha256([fragment]),
                expected_effective_from=date(2026, 8, 4),
                expected_effective_to=None,
                source_is_official=False,
                official_text_compared=True,
                artifact_is_complete=True,
                effective_dates_verified=True,
                fragments_verified=True,
            )
            with pytest.raises(ValueError, match="TRUSTED_COPY_MISREPRESENTED_AS_OFFICIAL"):
                await approve_legal_version(
                    factory, attestation.model_copy(update={"source_is_official": True})
                )
            approved_id = await approve_legal_version(factory, attestation)
            assert approved_id == version_id
            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                assert version.approval_state == "APPROVED"
                repository = ApprovedLegalCorpusRepository(session)
                library = await repository.list_documents(as_of_date=date(2026, 9, 12))
                assert any(item.version_id == version_id for item in library)
                evidence = await retrieve_planned_evidence(
                    repository, queries=[fragment_text], as_of_date=date(2026, 9, 12)
                )
                assert any(item.version_id == version_id for item in evidence)
                assert all(item.fragment_text == fragment_text for item in evidence)
                assert not await repository.search(fragment_text, as_of_date=date(2026, 8, 3))
        finally:
            await engine.dispose()

    asyncio.run(scenario())
