"""Bounded lexical batches must use one guarded snapshot, not one per query."""

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from legal_core.legal_retrieval import ApprovedLegalCorpusRepository


def test_lexical_batch_executes_one_guarded_statement(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: [])))
    repository = ApprovedLegalCorpusRepository(session)
    monkeypatch.setattr(repository, "_provider", lambda: None)

    async def scenario():
        results = await repository.search_many(
            ("платные услуги", "возврат", "не найдено"),
            as_of_date=date(2026, 10, 12), limit=5, semantic=(True, False, True),
        )
        assert results == [[], [], []]
        session.execute.assert_awaited_once()
        statement, params = session.execute.await_args.args
        assert "AS MATERIALIZED" in str(statement)
        assert "production_legal_fragments" in str(statement)
        assert str(statement).count("to_tsvector('russian', fragment_text)") == 1
        assert params["queries"] == ["платные услуги", "возврат", "не найдено"]
        assert params["as_of_date"] == date(2026, 10, 12)

    asyncio.run(scenario())


def test_semantic_batch_preserves_existing_per_query_privacy_and_ranking(monkeypatch):
    repository = ApprovedLegalCorpusRepository(SimpleNamespace())
    monkeypatch.setattr(repository, "_provider", lambda: object())
    calls = AsyncMock(return_value=[])
    monkeypatch.setattr(repository, "search", calls)

    asyncio.run(repository.search_many(
        ("платные услуги", "вымышленное описание услуги"),
        as_of_date=date(2026, 10, 12), limit=5, semantic=(True, False),
    ))
    assert calls.await_count == 2
    assert [call.kwargs["semantic"] for call in calls.await_args_list] == [True, False]


@pytest.mark.parametrize(("queries", "flags", "limit"), [
    ((" ",), (False,), 5),
    (("x" * 501,), (False,), 5),
    (("x",) * 21, (False,) * 21, 5),
    (("x",), (), 5),
    (("x",), (False,), 0),
    (("x",), (False,), 21),
])
def test_invalid_batches_rejected_before_database(queries, flags, limit):
    session = SimpleNamespace(execute=AsyncMock())
    repository = ApprovedLegalCorpusRepository(session)
    with pytest.raises(ValueError):
        asyncio.run(repository.search_many(
            queries, as_of_date=date(2026, 10, 12), limit=limit, semantic=flags,
        ))
    session.execute.assert_not_awaited()


def test_empty_batch_never_queries_database():
    session = SimpleNamespace(execute=AsyncMock())
    repository = ApprovedLegalCorpusRepository(session)
    assert asyncio.run(repository.search_many(
        (), as_of_date=date(2026, 10, 12), limit=5, semantic=(),
    )) == []
    session.execute.assert_not_awaited()
