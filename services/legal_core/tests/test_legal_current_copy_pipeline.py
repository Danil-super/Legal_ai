"""Disposable PostgreSQL coverage for synthetic v5 and legacy v2 legal evidence."""

import asyncio
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    ingest_manifest,
    normalized_text_sha256,
)
from legal_core.database import database_url
from legal_core.legal_approval import (
    ApprovalAttestation,
    approve_legal_version,
    approve_legal_version_in_session,
)
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository
from legal_core.models import LegalApprovalEvent, LegalDocument, LegalSource, LegalVersion, User

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL current-copy pipeline tests",
)

COPY_VALID_FROM = date(2026, 10, 8)
EFFECTIVE_TO = date(2027, 10, 8)
LIMITATIONS = ["Synthetic graphics and formula blocks are excluded from the text layer."]


def _write_manifest(tmp_path: Path, *, current_copy: bool) -> tuple[Path, str]:
    """Write only a generated fixture; the allowlisted URL is metadata, never fetched."""

    identifier = uuid4()
    suffix = identifier.hex
    query = f"syntheticpipeline{suffix}"
    fragment_text = f"Synthetic legal evidence {query}; no graphic or formula dependency."
    normalized = f"Synthetic document heading. {fragment_text} End of text."
    fragment = CorpusFragment(
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading=None,
        structural_path="point:1",
        text=fragment_text,
    )
    raw = (
        f"{{\\rtf1\\ansi Synthetic raw artifact {suffix}.}}".encode()
        if current_copy
        else f"%PDF-1.7\nSynthetic official artifact {suffix}.\n%%EOF\n".encode()
    )
    artifact = tmp_path / ("synthetic-copy.rtf" if current_copy else "synthetic-official.pdf")
    artifact.write_bytes(raw)
    host = "internet.garant.ru" if current_copy else "example.gov.ru"
    payload: dict[str, Any] = {
        "manifest_version": "dental-legal-corpus.v5" if current_copy else "dental-legal-corpus.v2",
        "source_key": "garant" if current_copy else f"synthetic-pipeline-{suffix}",
        "source_revision": 1 + identifier.int % 2_000_000_000,
        "source_name": "Synthetic pipeline integration source",
        "source_trust_level": "VERIFIED_COPY" if current_copy else "PRIMARY",
        "source_base_url": f"https://{host}/",
        "source_url": f"https://{host}/synthetic-pipeline/{suffix}",
        "source_external_id": suffix,
        "allowed_hosts": [host],
        "document_key": f"synthetic-pipeline-{suffix}",
        "document_type": "SYNTHETIC_TEST",
        "title": "Synthetic unnumbered copy" if current_copy else "Synthetic dated legal document",
        "issuer": None if current_copy else "Synthetic integration authority",
        "official_number": None if current_copy else f"synthetic-{suffix}",
        "adoption_date": "2026-01-01",
        "publication_date": None if current_copy else "2026-01-02",
        "version_date": None if current_copy else "2026-01-01",
        "effective_from": COPY_VALID_FROM.isoformat(),
        "effective_to": EFFECTIVE_TO.isoformat(),
        "approval_state": "REVIEW_REQUIRED",
        "artifact_kind": "THIRD_PARTY_VERIFIED_COPY" if current_copy else "OFFICIAL_RAW",
        "artifact_mime_type": "application/rtf" if current_copy else "application/pdf",
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "artifact_path": artifact.name,
        "artifact_retrieved_at": "2026-10-08T00:00:00Z",
        "artifact_size_bytes": len(raw),
        "artifact_page_count": None if current_copy else 1,
        "normalized_text": normalized,
        "normalized_sha256": normalized_text_sha256(normalized),
        "fragments_sha256": corpus_fragments_sha256([fragment]),
        "normalization_scope": "TEXT_LAYER" if current_copy else "FULL_DOCUMENT",
        "parser_version": "synthetic-pipeline.v1",
        "fragments": [fragment.model_dump(mode="json")],
    }
    if current_copy:
        payload.update(
            date_basis="LAWYER_CURRENT_COPY",
            copy_valid_from=COPY_VALID_FROM.isoformat(),
            extraction_limitations=LIMITATIONS,
        )
    manifest = tmp_path / "synthetic-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest, query


async def _reviewer(factory: async_sessionmaker[AsyncSession]) -> User:
    async with factory() as session, session.begin():
        reviewer = User(
            telegram_user_id=8_000_000_000 + uuid4().int % 1_000_000_000,
            display_name="Synthetic current-copy integration reviewer",
            system_role="LEGAL_EDITOR",
            status="ACTIVE",
        )
        session.add(reviewer)
        await session.flush()
    return reviewer


def _attestation(
    version: LegalVersion, reviewer: User, *, current_copy: bool, **changes: Any
) -> ApprovalAttestation:
    payload: dict[str, Any] = {
        "reviewer_telegram_user_id": reviewer.telegram_user_id,
        "version_id": version.id,
        "expected_sha256": version.raw_sha256,
        "expected_normalized_sha256": version.normalized_sha256,
        "expected_fragments_sha256": version.fragments_sha256,
        "expected_effective_from": version.effective_from,
        "expected_effective_to": version.effective_to,
        "source_is_official": not current_copy,
        "official_text_compared": True,
        "artifact_is_complete": True,
        "effective_dates_verified": not current_copy,
        "fragments_verified": True,
    }
    if current_copy:
        payload.update(current_copy_confirmed=True, extraction_limits_understood=True)
    return ApprovalAttestation.model_validate({**payload, **changes})


@pytest.mark.parametrize("current_copy", [True, False], ids=["v5-current-copy", "v2-dated-edition"])
def test_human_approval_pipeline_preserves_visibility_dates_and_policy(
    tmp_path: Path, current_copy: bool
) -> None:
    manifest, query = _write_manifest(tmp_path, current_copy=current_copy)

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            version_id = await ingest_manifest(factory, manifest)
            reviewer = await _reviewer(factory)

            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                source = await session.get(LegalSource, version.source_id)
                document = await session.get(LegalDocument, version.document_id)
                assert source is not None and document is not None
                assert version.approval_state == "REVIEW_REQUIRED"
                assert source.status == "DRAFT"
                assert version.approved_by is None and version.approved_at is None
                assert version.regression_passed is False
                assert version.effective_from == COPY_VALID_FROM
                assert version.date_basis == (
                    "LAWYER_CURRENT_COPY" if current_copy else "DATED_EDITION"
                )
                assert version.extraction_limitations == (LIMITATIONS if current_copy else [])
                assert version.normalization_scope == (
                    "TEXT_LAYER" if current_copy else "FULL_DOCUMENT"
                )
                if current_copy:
                    assert document.issuer is None and document.official_number is None
                    assert version.publication_date is None and version.version_date is None
                events = await session.scalar(
                    select(func.count(LegalApprovalEvent.id)).where(
                        LegalApprovalEvent.legal_version_id == version_id
                    )
                )
                assert events == 0
                pending = await ApprovedLegalCorpusRepository(session).search(
                    query, as_of_date=COPY_VALID_FROM
                )
                assert pending == []
                attestation = _attestation(version, reviewer, current_copy=current_copy)

            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                version.approval_state = "APPROVED"
                version.regression_passed = True
                version.approved_by = reviewer.id
                version.approved_at = datetime.now(UTC)
                with pytest.raises(DBAPIError, match="current approval event"):
                    await session.flush()
                await session.rollback()

            approved_id = await approve_legal_version(factory, attestation)
            replay_id = await approve_legal_version(factory, attestation)
            assert approved_id == replay_id == version_id

            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                source = await session.get(LegalSource, version.source_id)
                assert source is not None and source.status == "APPROVED"
                assert version.approval_state == "APPROVED"
                assert version.regression_passed is True
                assert version.approved_by == reviewer.id
                events = list(
                    await session.scalars(
                        select(LegalApprovalEvent).where(
                            LegalApprovalEvent.legal_version_id == version_id
                        )
                    )
                )
                assert len(events) == 1
                event = events[0]
                policy = "dental-legal-approval.v3" if current_copy else "dental-legal-approval.v2"
                assert event.decision == "APPROVED"
                assert event.policy_version == policy
                assert event.regression_checks_json["policyVersion"] == policy
                assert event.checks_json["effectiveDatesVerified"] is (not current_copy)
                if current_copy:
                    assert event.checks_json["currentCopyConfirmed"] is True
                    assert event.checks_json["extractionLimitsUnderstood"] is True
                assert version.publication_date == (None if current_copy else date(2026, 1, 2))

                repository = ApprovedLegalCorpusRepository(session)
                current = await repository.search(query, as_of_date=COPY_VALID_FROM)
                before_floor = await repository.search(
                    query, as_of_date=COPY_VALID_FROM - timedelta(days=1)
                )
                before_end = await repository.search(
                    query, as_of_date=EFFECTIVE_TO - timedelta(days=1)
                )
                expired = await repository.search(query, as_of_date=EFFECTIVE_TO)
                assert [hit.version_id for hit in current] == [version_id]
                assert [hit.version_id for hit in before_end] == [version_id]
                assert before_floor == []
                assert expired == []
                assert current[0].effective_from == COPY_VALID_FROM
                assert current[0].effective_to == EFFECTIVE_TO
                if current_copy:
                    assert current[0].issuer is None and current[0].official_number is None
                    assert current[0].publication_date is None and current[0].version_date is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_legacy_v2_approval_cannot_substitute_copy_flags_for_date_verification(
    tmp_path: Path,
) -> None:
    manifest, query = _write_manifest(tmp_path, current_copy=False)

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            version_id = await ingest_manifest(factory, manifest)
            reviewer = await _reviewer(factory)
            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                with pytest.raises(ValueError):
                    attestation = _attestation(
                        version,
                        reviewer,
                        current_copy=False,
                        effective_dates_verified=False,
                        current_copy_confirmed=True,
                        extraction_limits_understood=True,
                    )
                    await approve_legal_version_in_session(
                        session,
                        attestation,
                        require_review_required=True,
                        record_rejected_attempt=False,
                    )
                await session.rollback()

            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None and version.approval_state == "REVIEW_REQUIRED"
                count = await session.scalar(
                    select(func.count(LegalApprovalEvent.id)).where(
                        LegalApprovalEvent.legal_version_id == version_id
                    )
                )
                assert count == 0
                assert await ApprovedLegalCorpusRepository(session).search(
                    query, as_of_date=COPY_VALID_FROM
                ) == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())
