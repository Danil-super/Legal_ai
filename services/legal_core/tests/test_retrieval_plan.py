import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import UUID

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


def test_guided_v2_uses_typed_topics_without_free_text_or_legacy_labels() -> None:
    queries = plan_legal_queries(
        {
            FactKey.INTAKE_VERSION: "GUIDED_V2",
            FactKey.SITUATION_AREAS: ["PERSONAL_DATA", "MEDICAL_RECORDS"],
            FactKey.EVENT_SUMMARY: "SYNTHETIC_CANARY event narrative",
            FactKey.AFFECTED_SERVICES: ["SYNTHETIC_CANARY service"],
            FactKey.SERVICE_TYPE: "SYNTHETIC_CANARY legacy service",
            FactKey.FORMAL_CLAIM: "YES",
        }
    )

    assert {"врачебная тайна", "персональные данные",
            "медицинская документация пациент копии"} <= set(queries)
    assert "требования потребителя претензия медицинские услуги" not in queries
    assert all("SYNTHETIC_CANARY" not in query for query in queries)
    assert all(is_semantic_safe_query(query) for query in queries)


def test_guided_v2_uses_positive_health_signals_not_unknown_or_no_information() -> None:
    for signals in (["UNKNOWN"], ["NO_KNOWN_INFORMATION"], ["OTHER_CLINIC"], []):
        queries = plan_legal_queries(
            {FactKey.INTAKE_VERSION: "GUIDED_V2",
             FactKey.HEALTH_CONSEQUENCE_SIGNALS: signals}
        )
        assert "возмещение вреда здоровью медицинские услуги" not in queries

    for signal in ("COMPLICATION_OR_WORSENING", "HOSPITALIZATION"):
        queries = plan_legal_queries(
            {FactKey.INTAKE_VERSION: "GUIDED_V2",
             FactKey.HEALTH_CONSEQUENCE_SIGNALS: [signal]}
        )
        assert "возмещение вреда здоровью медицинские услуги" in queries


def test_guided_v2_actions_expand_only_relevant_fixed_search_candidates() -> None:
    queries = plan_legal_queries(
        {FactKey.INTAKE_VERSION: "GUIDED_V2",
         FactKey.CLINIC_ACTIONS: ["OFFERED_REFUND", "OFFERED_CORRECTION"]}
    )

    assert {"возврат денежных средств медицинские услуги",
            "безвозмездное устранение недостатков",
            "повторное выполнение работы"} <= set(queries)
    assert len(queries) == len(set(queries))
    assert all(is_semantic_safe_query(query) for query in queries)


def test_guided_v2_unknowns_and_all_known_categories_keep_plan_bounded() -> None:
    sparse = plan_legal_queries(
        {FactKey.INTAKE_VERSION: "GUIDED_V2",
         FactKey.INCOMING_COMMUNICATION: "UNKNOWN",
         FactKey.SITUATION_AREAS: ["OTHER"],
         FactKey.CLINIC_ACTIONS: ["NOTHING_YET"],
         FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["UNKNOWN"]}
    )
    assert sparse == (
        "платные медицинские услуги",
        "права пациента медицинская помощь",
        "ответственность исполнитель медицинские услуги",
    )

    dense = plan_legal_queries(
        {FactKey.INTAKE_VERSION: "GUIDED_V2",
         FactKey.INCOMING_COMMUNICATION: "AUTHORITY_OR_COURT_DOCUMENT",
         FactKey.SITUATION_AREAS: ["TREATMENT", "SERVICE", "MEDICAL_RECORDS",
                                   "PERSONAL_DATA", "OTHER"],
         FactKey.CLINIC_ACTIONS: ["OFFERED_REFUND", "OFFERED_CORRECTION",
                                  "REMOVED_MATERIAL", "OTHER"],
         FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["COMPLICATION_OR_WORSENING",
                                               "HOSPITALIZATION", "OTHER_CLINIC"]}
    )
    assert len(dense) == len(set(dense)) == 10
    assert all(is_semantic_safe_query(query) for query in dense)
    assert "ответственность медицинская организация проверка" not in dense


def test_guided_v2_absent_topics_and_unknown_incoming_add_no_positive_legal_signal() -> None:
    queries = plan_legal_queries(
        {FactKey.INTAKE_VERSION: "GUIDED_V2",
         FactKey.INCOMING_COMMUNICATION: "UNKNOWN",
         FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["UNKNOWN"],
         FactKey.FORMAL_CLAIM: "YES",
         FactKey.REGULATOR_OR_COURT: "YES",
         FactKey.HARM_CLAIMED: "YES"}
    )

    assert "возмещение вреда здоровью медицинские услуги" not in queries
    assert "требования потребителя претензия медицинские услуги" not in queries
    assert "ответственность медицинская организация проверка" not in queries


def test_legacy_query_plan_ignores_guided_v2_fields_without_v2_marker() -> None:
    queries = plan_legal_queries(
        {FactKey.SITUATION_AREAS: ["PERSONAL_DATA"],
         FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["HOSPITALIZATION"],
         FactKey.SERVICE_TYPE: "установка винира"}
    )

    assert "врачебная тайна" not in queries
    assert "возмещение вреда здоровью медицинские услуги" not in queries
    assert "медицинская услуга установка винира" in queries


def test_legacy_query_output_remains_exact_for_existing_fact_combination() -> None:
    queries = plan_legal_queries(
        {FactKey.FORMAL_CLAIM: "YES", FactKey.HARM_CLAIMED: "UNKNOWN",
         FactKey.REGULATOR_OR_COURT: "YES", FactKey.REGULATOR_THREAT: "UNKNOWN",
         FactKey.PATIENT_DEMAND: ["REFUND_DEMAND", "REWORK_DEMAND"],
         FactKey.INCIDENT_TYPES: ["QUALITY_COMPLAINT", "PERSONAL_DATA"],
         FactKey.SERVICE_TYPE: "установка винира"}
    )

    assert queries == (
        "платные медицинские услуги",
        "права пациента медицинская помощь",
        "ответственность исполнитель медицинские услуги",
        "требования потребителя претензия медицинские услуги",
        "ответственность медицинская организация проверка",
        "возврат денежных средств медицинские услуги",
        "безвозмездное устранение недостатков",
        "повторное выполнение работы",
        "недостатки оказанной услуги",
        "гарантийный срок",
        "врачебная тайна",
        "персональные данные",
        "медицинская услуга установка винира",
    )


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


def test_general_queries_cannot_exclude_later_scenario_queries() -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def search(self, query, **kwargs):
            self.calls.append(query)
            offset = int(query) * 10
            return [SimpleNamespace(fragment_id=UUID(int=offset + rank)) for rank in range(1, 6)]

    async def scenario() -> None:
        repository = FakeRepository()
        result = await retrieve_planned_evidence(  # type: ignore[arg-type]
            repository, queries=[str(index) for index in range(6)],
            as_of_date=date(2026, 9, 1), max_fragments=20,
        )
        assert repository.calls == [str(index) for index in range(6)]
        assert len(result) == 20
        assert [item.fragment_id for item in result[:6]] == [
            UUID(int=index * 10 + 1) for index in range(6)
        ]

    asyncio.run(scenario())
