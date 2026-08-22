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
from legal_core.legal_approval import ApprovalAttestation, approve_legal_version
from legal_core.models import (
    LegalApprovalEvent,
    LegalFragment,
    LegalSource,
    LegalVersion,
    User,
)
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
