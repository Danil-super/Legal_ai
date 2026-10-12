"""Synthetic PostgreSQL reproduction of full-version approval/read timeouts.

Each scenario rolls back its own rows, including immutable approval events. A two-second
per-version budget leaves room for a nine-version approval within the gateway's 20 seconds.
No source text, patient data, external fetching or real legal approvals are involved.
"""

import asyncio
import hashlib
import os
from datetime import UTC, date, datetime
from time import perf_counter
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    normalized_text_sha256,
)
from legal_core.database import database_url
from legal_core.legal_approval import (
    approve_legal_version_in_session,
    potential_approval_attestation,
)
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository
from legal_core.models import LegalDocument, LegalFragment, LegalSource, LegalVersion, User

_FRAGMENT_COUNT = 1_500
_SECONDS_PER_VERSION = 2.0
_EFFECTIVE_FROM = date(2026, 1, 1)
_EFFECTIVE_TO = date(2027, 1, 1)


async def _seed_large_version(
    session: AsyncSession, *, fragment_count: int = _FRAGMENT_COUNT, digest_bytes: int = 600
) -> tuple[LegalVersion, User]:
    """Keep the large UTF-8 corpus valid and poorly compressible for TOAST realism."""
    identity = uuid4()
    reviewer = User(
        telegram_user_id=8_000_000_000 + identity.int % 1_000_000_000,
        display_name="Synthetic performance legal editor",
        status="ACTIVE",
        system_role="LEGAL_EDITOR",
    )
    source = LegalSource(
        source_key=f"synthetic-performance-{identity}",
        revision=1,
        display_name="Synthetic performance source",
        base_url="https://synthetic.example.test/performance",
        allowed_hosts=["synthetic.example.test"],
        trust_level="VERIFIED_COPY",
        status="DRAFT",
    )
    document = LegalDocument(
        canonical_key=f"synthetic-performance-{identity}",
        document_type="SYNTHETIC_TEST",
        title=f"Synthetic performance document {identity}",
        issuer=None,
        official_number=None,
        adoption_date=_EFFECTIVE_FROM,
    )
    session.add_all([reviewer, source, document])
    await session.flush()
    fragments = [
        CorpusFragment(
            ordinal=ordinal,
            article=str(ordinal),
            part=None,
            point=None,
            heading=None,
            structural_path=f"synthetic/article:{ordinal}",
            text=(
                f"Вымышленный фрагмент для проверки производительности {ordinal:04d}. "
                + hashlib.shake_256(f"synthetic-{ordinal}".encode()).hexdigest(digest_bytes)
            ),
        )
        for ordinal in range(1, fragment_count + 1)
    ]
    normalized = "\n".join(fragment.text for fragment in fragments)
    raw = b"{\\rtf1\\ansi\\ansicpg65001 " + normalized.encode("utf-8") + b"}"
    assert 1_800_000 <= len(normalized.encode("utf-8")) <= 2_000_000
    assert raw.decode("utf-8").startswith("{\\rtf1")
    version = LegalVersion(
        document_id=document.id,
        source_id=source.id,
        version_no=1,
        source_external_id=str(identity),
        source_url=source.base_url,
        publication_date=None,
        version_date=None,
        effective_from=_EFFECTIVE_FROM,
        effective_to=_EFFECTIVE_TO,
        approval_state="REVIEW_REQUIRED",
        artifact_kind="THIRD_PARTY_VERIFIED_COPY",
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        raw_mime_type="application/rtf",
        raw_bytes=raw,
        raw_size_bytes=len(raw),
        artifact_retrieved_at=datetime.now(UTC),
        normalized_text=normalized,
        normalized_sha256=normalized_text_sha256(normalized),
        fragments_sha256=corpus_fragments_sha256(fragments),
        normalization_scope="TEXT_LAYER",
        parser_version="synthetic-performance.v1",
        date_basis="LAWYER_CURRENT_COPY",
        extraction_limitations=["Fully synthetic performance fixture, not legal evidence."],
    )
    session.add(version)
    await session.flush()
    session.add_all(
        [
            LegalFragment(
                version_id=version.id,
                ordinal=fragment.ordinal,
                article=fragment.article,
                part=None,
                point=None,
                heading=None,
                structural_path=fragment.structural_path,
                fragment_text=fragment.text,
                text_sha256=hashlib.sha256(fragment.text.encode("utf-8")).hexdigest(),
            )
            for fragment in fragments
        ]
    )
    await session.flush()
    return version, reviewer


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL performance regressions",
)
@pytest.mark.parametrize("operation", ["approval", "library"])
@pytest.mark.parametrize(("fragment_count", "digest_bytes"), [(1_500, 600), (9_000, 53)])
def test_large_current_copy_approval_and_library_fit_gateway_budget(
    operation: str, fragment_count: int, digest_bytes: int
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await session.begin()
                try:
                    version, reviewer = await _seed_large_version(
                        session, fragment_count=fragment_count, digest_bytes=digest_bytes
                    )
                    repository = ApprovedLegalCorpusRepository(session)
                    pending = await repository.list_documents(as_of_date=_EFFECTIVE_FROM)
                    assert all(item.version_id != version.id for item in pending)
                    attestation = potential_approval_attestation(version, reviewer.telegram_user_id)
                    started = perf_counter()
                    approved = await approve_legal_version_in_session(session, attestation)
                    await session.flush()
                    approval_seconds = perf_counter() - started
                    assert approved.id == version.id
                    assert approved.approval_state == "APPROVED"
                    assert approved.regression_passed
                    assert await session.scalar(
                        text(
                            "SELECT legal_approval_event_is_current(ae) "
                            "FROM legal_approval_events ae WHERE ae.legal_version_id = :id"
                        ),
                        {"id": version.id},
                    ) is True

                    started = perf_counter()
                    documents = await repository.list_documents(as_of_date=_EFFECTIVE_FROM)
                    library_seconds = perf_counter() - started
                    matching = [item for item in documents if item.version_id == version.id]
                    assert len(matching) == 1
                    assert matching[0].fragment_count == fragment_count
                    assert matching[0].raw_sha256 == version.raw_sha256
                    expired = await repository.list_documents(as_of_date=_EFFECTIVE_TO)
                    assert all(item.version_id != version.id for item in expired)

                    async with session.begin_nested() as savepoint:
                        with pytest.raises(DBAPIError, match="append-only"):
                            await session.execute(
                                text(
                                    "UPDATE legal_approval_events SET reason_code = 'FORGED' "
                                    "WHERE legal_version_id = :id"
                                ),
                                {"id": version.id},
                            )
                        await savepoint.rollback()

                    elapsed = approval_seconds if operation == "approval" else library_seconds
                    print(
                        f"synthetic={len(version.raw_bytes)} bytes/{fragment_count} fragments; "
                        f"approval={approval_seconds:.3f}s; library={library_seconds:.3f}s"
                    )
                    assert elapsed < _SECONDS_PER_VERSION, (
                        f"{operation} took {elapsed:.3f}s for one synthetic version; "
                        f"budget {_SECONDS_PER_VERSION:.1f}s per version preserves headroom "
                        "for nine-version batches under the 20s gateway timeout"
                    )
                finally:
                    await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
def test_batched_retrieval_preserves_single_query_results_and_dates():
    async def scenario():
        engine = create_async_engine(database_url())
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
                version, reviewer = await _seed_large_version(session)
                repository = ApprovedLegalCorpusRepository(session)
                queries = ("Вымышленный", "фрагмент проверки", "absent-canary")
                pending = await repository.search_many(
                    queries, as_of_date=_EFFECTIVE_FROM, limit=5, semantic=(False,) * 3
                )
                assert not any(item.version_id == version.id for hits in pending for item in hits)
                await approve_legal_version_in_session(
                    session, potential_approval_attestation(version, reviewer.telegram_user_id)
                )
                await session.flush()
                single = [await repository.search(q, as_of_date=_EFFECTIVE_FROM, limit=5)
                          for q in queries]
                batched = await repository.search_many(
                    queries, as_of_date=_EFFECTIVE_FROM, limit=5, semantic=(False,) * 3
                )
                assert batched == single
                assert any(item.version_id == version.id for hits in batched for item in hits)
                expired = await repository.search_many(
                    queries, as_of_date=_EFFECTIVE_TO, limit=5, semantic=(False,) * 3
                )
                assert not any(item.version_id == version.id for hits in expired for item in hits)
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
