"""Broad medical foundation retrieval is distinct from targeted labour/tax research."""

import asyncio
import hashlib
import json
import os
from datetime import date
from time import perf_counter
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from legal_core.database import database_url
from legal_core.embedding_indexer import index_approved_fragments
from legal_core.legal_approval import (
    approve_legal_version_in_session,
    potential_approval_attestation,
)
from legal_core.legal_retrieval import MEDICAL_FOUNDATION_QUERIES, ApprovedLegalCorpusRepository
from legal_core.models import User
from legal_core.retrieval_plan import plan_legal_queries, retrieve_planned_evidence
from test_case_api import actor_headers, application_client
from test_legal_retrieval import FakeEmbeddingProvider
from test_legal_editor_workspace_api import _ingest, _seed_user, _version_payload, _write_manifest
from test_large_legal_approval_performance import _EFFECTIVE_FROM, _seed_large_version
from test_verified_copy_analysis_e2e import EDITOR_KEY, isolated_database as isolated_database

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


def test_broad_queries_exclude_unrelated_families_but_targeted_queries_keep_them(
    isolated_database,
    tmp_path,
):
    common = (
        "Синтетическая учебная норма. Платные медицинские услуги. "
        "Права пациента медицинская помощь. "
        "Ответственность исполнитель медицинские услуги. "
    )
    specs = [
        ("Правила предоставления платных медицинских услуг", "Договор и информация."),
        ("Закон об основах охраны здоровья", "Условия оказания помощи и права пациента."),
        (
            "Закон о защите прав потребителей",
            "Безвозмездное устранение недостатков. "
            "Повторное выполнение работы. Возврат уплаченной суммы при соблюдении условий. "
            "Исполнитель освобождается от ответственности при доказанности исключения.",
        ),
        ("Гражданский кодекс", "Возмещение убытков при установлении оснований."),
        ("Постановление Пленума о защите прав потребителей", "Бремя доказывания."),
        (
            "Об утверждении профессионального стандарта Врач",
            "Квалификация врача. Трудовые обязанности.",
        ),
        (
            "Об утверждении профессионального стандарта Медицинская сестра",
            "Квалификация сестры. Трудовые обязанности.",
        ),
        ("Налоговый кодекс Российской Федерации", "Налоговый вычет."),
    ]
    _seed_user(103_900_000_002, system_role="LEGAL_EDITOR")
    versions = []
    with application_client() as client:
        for title, body in specs:
            path = _write_manifest(tmp_path, fragment_text=common + body)
            manifest = json.loads(path.read_text())
            manifest["title"] = title
            path.write_text(json.dumps(manifest, ensure_ascii=False))
            version = _ingest(path)
            versions.append(version)
            response = client.post(
                f"/v1/legal/review-queue/{version}/approval-events",
                headers={
                    **actor_headers(103_900_000_002, uuid4()),
                    "X-Legal-Editor-Gateway-Key": EDITOR_KEY,
                },
                json=_version_payload(version),
            )
            assert response.status_code == 200, response.text

    async def scenario():
        engine = create_async_engine(database_url())
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            provider = FakeEmbeddingProvider()
            await index_approved_fragments(
                factory, provider=provider, batch_size=8, max_fragments=50
            )
            async with factory() as session:
                repo = ApprovedLegalCorpusRepository(session)
                queries = plan_legal_queries({})
                baseline = await repo.search(
                    "синтетическая учебная норма", as_of_date=date(2026, 10, 27)
                )
                assert {item.version_id for item in baseline} == set(versions)
                broad = await retrieve_planned_evidence(
                    repo, queries=queries, as_of_date=date(2026, 10, 27), limit_per_query=10
                )
                assert {item.version_id for item in broad} == set(versions[:5])
                assert sum(len(item.fragment_text) for item in broad) < sum(
                    len(common + body) for _, body in specs
                )
                print(
                    f"synthetic precision fragments={len(specs)}->{len(broad)}; "
                    f"characters={sum(len(common + body) for _, body in specs)}->"
                    f"{sum(len(item.fragment_text) for item in broad)}"
                )
                assert {item.fragment_text for item in broad} == {
                    common + body for _, body in specs[:5]
                }
                # Preserve full text, conditions, dates, limitations and provenance together.
                retained = [item for item in baseline if item.version_id in versions[:5]]
                assert sorted(broad, key=lambda item: item.fragment_id) == sorted(
                    retained, key=lambda item: item.fragment_id
                )
                for query, expected in (
                    ("квалификация", versions[5:7]),
                    ("трудовые обязанности", versions[5:7]),
                    ("налоговый вычет", versions[7:]),
                ):
                    targeted = await repo.search(query, as_of_date=date(2026, 10, 27))
                    assert {item.version_id for item in targeted} == set(expected)
                    mixed = await repo.search_many(
                        (queries[0], query),
                        as_of_date=date(2026, 10, 27),
                        limit=10,
                        semantic=(False, False),
                    )
                    assert {item.version_id for item in mixed[0]} == set(versions[:5])
                    assert mixed[1] == targeted
                semantic_repo = ApprovedLegalCorpusRepository(session, embedding_provider=provider)
                semantic_hits = await semantic_repo.search_many(
                    queries,
                    as_of_date=date(2026, 10, 27),
                    limit=10,
                    semantic=(True,) * len(queries),
                )
                assert all(
                    {item.version_id for item in hits} == set(versions[:5])
                    for hits in semantic_hits
                )
                semantic_targeted = await semantic_repo.search(
                    "квалификация", as_of_date=date(2026, 10, 27), limit=20, semantic=True
                )
                assert set(versions[5:7]) <= {item.version_id for item in semantic_targeted}
                for query in queries:
                    single = await repo.search(query, as_of_date=date(2026, 10, 27))
                    assert {item.version_id for item in single} == set(versions[:5])
                expired = await repo.search_many(
                    queries, as_of_date=date(2027, 2, 1), limit=10, semantic=(False,) * len(queries)
                )
                assert expired == [[], [], []]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_seven_query_batch_keeps_complete_large_fragments_within_local_read_budget():
    async def scenario():
        engine = create_async_engine(database_url())
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
                version, reviewer = await _seed_large_version(session)
                await approve_legal_version_in_session(
                    session, potential_approval_attestation(version, reviewer.telegram_user_id)
                )
                await session.flush()
                repo = ApprovedLegalCorpusRepository(session)
                queries = (
                    "вымышленный фрагмент",
                    "фрагмент проверки",
                    "проверка производительности",
                    "вымышленный",
                    "фрагмент",
                    "производительность",
                    "отсутствующий",
                )
                started = perf_counter()
                batch = await repo.search_many(
                    queries, as_of_date=_EFFECTIVE_FROM, limit=5, semantic=(False,) * len(queries)
                )
                elapsed = perf_counter() - started
                single = [
                    await repo.search(query, as_of_date=_EFFECTIVE_FROM, limit=5)
                    for query in queries
                ]
                assert batch == single
                assert len(batch[-1]) == 0
                assert any(item.version_id == version.id for hits in batch for item in hits)
                print(f"synthetic 1500-fragment/7-query batch={elapsed:.3f}s")
                # Coarse regression guard; microbenchmark medians are recorded in ADR 0123.
                assert elapsed < 2.0
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


class _LocalEmbeddingProvider:
    """Noise outranks useful evidence so vector LIMIT cannot hide a missing scope."""

    model_key = "integration:foundation-scope:v1"
    dimensions = 3

    async def embed(self, texts):
        return tuple(
            (1.0, 0.0, 0.0)
            if value in MEDICAL_FOUNDATION_QUERIES or "шум" in value
            else (0.0, 1.0, 0.0)
            for value in texts
        )


@pytest.mark.parametrize("semantic_only", [False, True])
def test_family_scope_precedes_limits_and_preserves_approved_source_gates(
    isolated_database,
    tmp_path,
    semantic_only,
):
    # Six stronger irrelevant candidates exceed search(limit=1)'s candidate window of three.
    noise = "Синтетический шум. " + ". ".join(MEDICAL_FOUNDATION_QUERIES) * 8
    useful = (
        "Синтетическая учебная норма: существенные условия и исключения. "
        "Упоминание профессионального стандарта и налогового кодекса в тексте допустимо."
    )
    if not semantic_only:
        useful += " " + ". ".join(MEDICAL_FOUNDATION_QUERIES)
    titles = [
        "Об утверждении профессионального стандарта Врач",
        "Профессиональный стандарт Медицинская сестра",
        "Профессиональные стандарты работников",
        "Налоговый кодекс Российской Федерации часть первая",
        "Налоговый кодекс Российской Федерации часть вторая",
        "НАЛОГОВЫЙ КОДЕКС тестовая редакция",
    ]
    _seed_user(103_900_000_002, system_role="LEGAL_EDITOR")
    with application_client() as client:
        for title, body in [(title, noise) for title in titles] + [
            ("Правила платных медицинских услуг", useful)
        ]:
            path = _write_manifest(tmp_path, fragment_text=body)
            manifest = json.loads(path.read_text())
            manifest["title"] = title
            path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            version = _ingest(path)
            response = client.post(
                f"/v1/legal/review-queue/{version}/approval-events",
                headers={
                    **actor_headers(103_900_000_002, uuid4()),
                    "X-Legal-Editor-Gateway-Key": EDITOR_KEY,
                },
                json=_version_payload(version),
            )
            assert response.status_code == 200, response.text
        expected = version

    async def scenario():
        engine = create_async_engine(database_url())
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            provider = _LocalEmbeddingProvider()
            await index_approved_fragments(
                factory, provider=provider, batch_size=8, max_fragments=50
            )
            async with factory() as session:
                repo = ApprovedLegalCorpusRepository(session, embedding_provider=provider)
                for query in MEDICAL_FOUNDATION_QUERIES:
                    lexical = await repo.search(query, as_of_date=date(2026, 10, 27), limit=1)
                    assert {item.version_id for item in lexical} == (
                        set() if semantic_only else {expected}
                    )
                    hybrid = await repo.search(
                        query, as_of_date=date(2026, 10, 27), limit=1, semantic=True
                    )
                    assert [item.version_id for item in hybrid] == [expected]
                    assert hybrid[0].fragment_text == useful
                    assert hybrid[0].text_sha256 == hashlib.sha256(useful.encode()).hexdigest()
                batched = await repo.search_many(
                    MEDICAL_FOUNDATION_QUERIES,
                    as_of_date=date(2026, 10, 27),
                    limit=1,
                    semantic=(False,) * 3,
                )
                assert [{item.version_id for item in hits} for hits in batched] == [
                    set() if semantic_only else {expected}
                ] * 3
                for boundary in (date(2026, 1, 31), date(2027, 2, 1)):
                    assert await repo.search_many(
                        MEDICAL_FOUNDATION_QUERIES,
                        as_of_date=boundary,
                        limit=1,
                        semantic=(True,) * 3,
                    ) == [[], [], []]
                # Current approval must be rechecked when the approving editor becomes inactive.
                editor = await session.scalar(
                    select(User).where(User.telegram_user_id == 103_900_000_002)
                )
                assert editor is not None
                editor.status = "BLOCKED"
                await session.flush()
                for semantic in (False, True):
                    assert await repo.search_many(
                        MEDICAL_FOUNDATION_QUERIES,
                        as_of_date=date(2026, 10, 27),
                        limit=1,
                        semantic=(semantic,) * 3,
                    ) == [[], [], []]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
