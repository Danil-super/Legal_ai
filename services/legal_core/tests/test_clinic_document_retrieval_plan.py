from legal_core.clinic_document_retrieval import plan_clinic_document_queries
from legal_core.contracts import FactKey


def test_implant_claim_and_records_queries_precede_generic_documents() -> None:
    queries = plan_clinic_document_queries(
        {
            FactKey.SERVICE_TYPE: "Имплантация",
            FactKey.FORMAL_CLAIM: "YES",
            FactKey.PATIENT_DEMAND: {
                "values": ["REFUND", "MEDICAL_RECORDS_REQUEST"]
            },
            FactKey.INCIDENT_TYPES: ["IMPLANT_PROBLEM"],
        }
    )

    assert queries[0] == "Имплантация"
    assert queries.index("имплант") < queries.index("договор")
    assert queries.index("претенз") < queries.index("договор")
    assert queries.index("возврат") < queries.index("договор")
    assert queries.index("документ") < queries.index("договор")
    assert len(queries) <= 8


def test_warranty_sensitive_incident_promotes_warranty_before_contract() -> None:
    queries = plan_clinic_document_queries(
        {
            FactKey.PRIMARY_INCIDENT_TYPE: "CROWN_PROBLEM",
            FactKey.SERVICE_TYPE: "",
        }
    )

    assert queries[0] == "гарант"
    assert queries.index("гарант") < queries.index("договор")
    assert queries.count("гарант") == 1


def test_free_text_service_query_is_local_bounded_and_deduplicated() -> None:
    queries = plan_clinic_document_queries(
        {
            FactKey.SERVICE_TYPE: "Ортодонтия " + "очень-длинное-описание-" * 20,
            FactKey.FORMAL_CLAIM: False,
        }
    )

    assert len(queries) <= 8
    assert len(queries) == len(set(queries))
    assert len(queries[0]) <= 120
    assert "ортодонт" in queries
    assert queries[-3:] == ("договор", "согласие", "гарант")
