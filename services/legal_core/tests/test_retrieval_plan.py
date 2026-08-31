import asyncio
from datetime import date

from legal_core.contracts import FactKey
from legal_core.retrieval_plan import (
    is_semantic_safe_query,
    plan_legal_queries,
    retrieve_planned_evidence,
)


def test_query_plan_expands_for_claim_harm_and_refund() -> None:
    queries = plan_legal_queries(
        {
            FactKey.FORMAL_CLAIM: "YES",
            FactKey.HARM_CLAIMED: "YES",
            FactKey.REGULATOR_THREAT: "YES",
            FactKey.PATIENT_DEMAND: ["REFUND_DEMAND"],
            FactKey.SERVICE_TYPE: "установка винира",
        }
    )

    assert "платные медицинские услуги" in queries
    assert "требования потребителя претензия медицинские услуги" in queries
    assert "возмещение вреда здоровью медицинские услуги" in queries
    assert "возврат денежных средств медицинские услуги" in queries
    assert "медицинская услуга установка винира" in queries
    assert len(queries) == len(set(queries))


def test_query_plan_is_bounded_for_long_service_text() -> None:
    queries = plan_legal_queries({FactKey.SERVICE_TYPE: "а" * 1_000})

    service_query = next(query for query in queries if query.startswith("медицинская услуга "))
    assert len(service_query) <= len("медицинская услуга ") + 120


def test_only_fixed_legal_queries_are_allowed_to_use_external_semantic_embedding() -> None:
    assert is_semantic_safe_query("платные медицинские услуги") is True
    assert is_semantic_safe_query("возврат денежных средств медицинские услуги") is True
    assert is_semantic_safe_query("медицинская услуга установка винира") is False
    assert is_semantic_safe_query("Иванов +7 999 123-45-67") is False


def test_retrieval_marks_free_text_service_query_lexical_only() -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        async def search(
            self,
            query: str,
            *,
            as_of_date: date,
            limit: int,
            semantic: bool = False,
        ) -> list[object]:
            del as_of_date, limit
            self.calls.append((query, semantic))
            return []

    async def scenario() -> None:
        repository = FakeRepository()
        queries = (
            "платные медицинские услуги",
            "медицинская услуга пациент Иванов установка винира",
        )
        result = await retrieve_planned_evidence(  # type: ignore[arg-type]
            repository,
            queries=queries,
            as_of_date=date(2026, 8, 31),
        )
        assert result == []
        assert repository.calls == [
            ("платные медицинские услуги", True),
            ("медицинская услуга пациент Иванов установка винира", False),
        ]

    asyncio.run(scenario())
