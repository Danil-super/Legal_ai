from legal_core.contracts import FactKey
from legal_core.retrieval_plan import plan_legal_queries


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
