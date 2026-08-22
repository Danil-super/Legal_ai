import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    ingest_manifest,
    normalized_text_sha256,
)
from legal_core.database import database_url
from legal_core.models import (
    LegalApprovalEvent,
    LegalFragment,
    LegalSource,
    LegalVersion,
    User,
)
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL legal guard tests",
)


def _write_official_manifest(tmp_path: Path) -> Path:
    suffix = uuid4().hex
    raw = f"%PDF-1.7\nofficial guard artifact {suffix}\n%%EOF\n".encode()
    artifact = tmp_path / f"official-{suffix}.pdf"
    artifact.write_bytes(raw)
    fragment_text = f"Complete official legal guard fragment for scenario {suffix}."
    normalized = f"Document heading. {fragment_text} End of complete document."
    fragment = CorpusFragment(
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading=None,
        structural_path="point:1",
        text=fragment_text,
    )
    manifest_path = tmp_path / f"manifest-{suffix}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "dental-legal-corpus.v2",
                "source_key": f"official-guard-{suffix}",
                "source_name": "Official guard integration source",
                "source_url": f"https://example.gov.ru/document/{suffix}",
                "source_external_id": suffix,
                "allowed_hosts": ["example.gov.ru"],
                "document_key": f"guard-document-{suffix}",
                "document_type": "DECREE",
                "title": "Guard integration legal document",
                "issuer": "Integration authority",
                "official_number": f"guard-{suffix}",
                "adoption_date": "2026-01-01",
                "publication_date": "2026-01-02",
                "version_date": "2026-01-01",
                "effective_from": "2026-02-01",
                "effective_to": "2027-02-01",
                "approval_state": "REVIEW_REQUIRED",
                "artifact_kind": "OFFICIAL_RAW",
                "artifact_mime_type": "application/pdf",
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                "artifact_path": artifact.name,
                "artifact_retrieved_at": "2026-08-22T00:00:00Z",
                "artifact_size_bytes": len(raw),
                "artifact_page_count": 1,
                "normalized_text": normalized,
                "normalized_sha256": normalized_text_sha256(normalized),
                "fragments_sha256": corpus_fragments_sha256([fragment]),
                "normalization_scope": "FULL_DOCUMENT",
                "parser_version": "legal-guard.v1",
                "fragments": [fragment.model_dump(mode="json")],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


async def _create_user(
    factory: async_sessionmaker[AsyncSession], *, role: str = "LEGAL_EDITOR"
) -> User:
    telegram_user_id = int(f"82{uuid4().int % 10**8:08d}")
    async with factory() as session, session.begin():
        user = User(
            telegram_user_id=telegram_user_id,
            display_name=f"Legal guard {role.lower()}",
            system_role=role,
        )
        session.add(user)
        await session.flush()
        identifier = user.id
    async with factory() as session:
        stored = await session.get(User, identifier)
        assert stored is not None
        return stored


def _approval_payload(version: LegalVersion, fragment_count: int) -> tuple[dict, dict]:
    checks = {
        "sourceIsOfficial": True,
        "artifactIsComplete": True,
        "effectiveDatesVerified": True,
        "fragmentsVerified": True,
        "expectedNormalizedSha256": version.normalized_sha256,
        "expectedFragmentsSha256": version.fragments_sha256,
        "expectedEffectiveFrom": version.effective_from.isoformat(),
        "expectedEffectiveTo": version.effective_to.isoformat() if version.effective_to else None,
    }
    regression = {
        "policyVersion": "dental-legal-approval.v1",
        "passed": True,
        "reasonCode": None,
        "rawShaMatches": True,
        "rawSizeMatches": True,
        "normalizedShaMatches": True,
        "fragmentsSha256": version.fragments_sha256,
        "fragmentCount": fragment_count,
        "normalizationScope": version.normalization_scope,
        "effectiveFrom": version.effective_from.isoformat(),
        "effectiveTo": version.effective_to.isoformat() if version.effective_to else None,
        "effectiveRangeValid": True,
        "paidMedicalServicesBoundary": None,
    }
    return checks, regression


async def _canonical_regression_digest(session: AsyncSession, payload: dict) -> str:
    digest = await session.scalar(
        text(
            "SELECT legal_regression_result_sha256(CAST(:payload AS jsonb))"
        ),
        {"payload": json.dumps(payload, separators=(",", ":"))},
    )
    assert isinstance(digest, str)
    return digest


async def _add_valid_approval_event(
    session: AsyncSession, version: LegalVersion, reviewer: User
) -> None:
    checks, regression = _approval_payload(version, fragment_count=1)
    session.add(
        LegalApprovalEvent(
            legal_version_id=version.id,
            actor_user_id=reviewer.id,
            decision="APPROVED",
            expected_sha256=version.raw_sha256,
            reason_code="HUMAN_LEGAL_REVIEW_PASSED",
            checks_json=checks,
            policy_version="dental-legal-approval.v1",
            regression_result_sha256=await _canonical_regression_digest(session, regression),
            regression_checks_json=regression,
        )
    )
    await session.flush()


def test_crafted_approval_event_cannot_unlock_a_version(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        version_id = await ingest_manifest(factory, _write_official_manifest(tmp_path))
        reviewer = await _create_user(factory)
        try:
            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                checks, regression = _approval_payload(version, fragment_count=999)
                digest = await _canonical_regression_digest(session, regression)
                session.add(
                    LegalApprovalEvent(
                        legal_version_id=version.id,
                        actor_user_id=reviewer.id,
                        decision="APPROVED",
                        expected_sha256=version.raw_sha256,
                        reason_code="HUMAN_LEGAL_REVIEW_PASSED",
                        checks_json=checks,
                        policy_version="dental-legal-approval.v1",
                        regression_result_sha256=digest,
                        regression_checks_json=regression,
                    )
                )
                version.approval_state = "APPROVED"
                version.regression_passed = True
                version.approved_by = reviewer.id
                version.approved_at = datetime.now(UTC)
                with pytest.raises(DBAPIError, match="approval event integrity"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_approval_event_requires_active_legal_editor(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        version_id = await ingest_manifest(factory, _write_official_manifest(tmp_path))
        ordinary_user = await _create_user(factory, role="CLINIC_ADMIN")
        try:
            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                with pytest.raises(DBAPIError, match="active LEGAL_EDITOR"):
                    await _add_valid_approval_event(session, version, ordinary_user)
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_source_cannot_be_approved_without_valid_approval_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        version_id = await ingest_manifest(factory, _write_official_manifest(tmp_path))
        reviewer = await _create_user(factory)
        try:
            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                source = await session.get(LegalSource, version.source_id)
                assert source is not None
                source.status = "APPROVED"
                source.approved_by = reviewer.id
                source.approved_at = datetime.now(UTC)
                with pytest.raises(DBAPIError, match="source approval transition"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_fragment_waiting_on_approval_lock_is_rechecked_after_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        version_id = await ingest_manifest(factory, _write_official_manifest(tmp_path))
        reviewer = await _create_user(factory)
        marker = f"legal-fragment-race-{uuid4().hex}"
        try:
            async def append_fragment() -> DBAPIError | None:
                try:
                    async with factory() as session, session.begin():
                        await session.execute(
                            text("SELECT set_config('application_name', :marker, true)"),
                            {"marker": marker},
                        )
                        late_text = (
                            "A late unreviewed fragment must never enter production retrieval."
                        )
                        session.add(
                            LegalFragment(
                                version_id=version_id,
                                ordinal=2,
                                article=None,
                                part=None,
                                point="2",
                                heading=None,
                                structural_path="point:2",
                                fragment_text=late_text,
                                text_sha256=hashlib.sha256(late_text.encode()).hexdigest(),
                            )
                        )
                        await session.flush()
                except DBAPIError as exc:
                    return exc
                return None

            async with factory() as approving_session, approving_session.begin():
                version = await approving_session.scalar(
                    select(LegalVersion).where(LegalVersion.id == version_id).with_for_update()
                )
                assert version is not None
                await _add_valid_approval_event(approving_session, version, reviewer)
                append_task = asyncio.create_task(append_fragment())

                blocked = False
                for _ in range(100):
                    async with factory() as monitor:
                        blocked = bool(
                            await monitor.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE application_name = :marker AND wait_event_type = 'Lock')"
                                ),
                                {"marker": marker},
                            )
                        )
                    if blocked:
                        break
                    await asyncio.sleep(0.02)
                assert blocked, "late fragment insert never reached the version lock"

                source = await approving_session.get(LegalSource, version.source_id)
                assert source is not None
                approved_at = datetime.now(UTC)
                version.approval_state = "APPROVED"
                version.regression_passed = True
                version.approved_by = reviewer.id
                version.approved_at = approved_at
                await approving_session.flush()
                source.status = "APPROVED"
                source.approved_by = reviewer.id
                source.approved_at = approved_at

            insert_error = await asyncio.wait_for(append_task, timeout=3)
            assert insert_error is not None
            assert "approved legal version" in str(insert_error)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
