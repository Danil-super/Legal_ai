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


def test_guided_v2_queries_use_confirmed_services_and_plain_factual_signals() -> None:
    facts = {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.SITUATION_AREAS: ["TREATMENT", "MEDICAL_RECORDS"],
        FactKey.AFFECTED_SERVICES: ["Имплантация зуба"],
        FactKey.FACTUAL_SAFETY_SCREENING: {
            "schemaVersion": "factual-safety-intake.v1",
            "healthDeteriorationReported": "NO", "hospitalizationReported": "NO",
            "representativeContact": "NO", "writtenRequirementsReceived": "YES",
            "authorityOrCourtDocumentReceived": "NO", "authorityReferralMentioned": "NO",
            "moneyRequested": "YES", "amount": {"currency": "RUB", "amountKopecks": 500000},
        },
    }
    original = {key: value for key, value in facts.items()}
    queries = plan_clinic_document_queries(facts)

    assert queries[0] == "Имплантация зуба"
    assert "имплант" in queries and "после имплант" in queries
    assert "претенз" in queries and "возврат" in queries and "документ" in queries
    assert len(queries) <= 8 and len(set(queries)) == len(queries)
    assert facts == original and FactKey.FORMAL_CLAIM not in facts


def test_guided_v2_never_uses_stale_legacy_service_or_unconfirmed_money() -> None:
    queries = plan_clinic_document_queries({
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.SITUATION_AREAS: ["SERVICE"],
        FactKey.SERVICE_TYPE: "Имплантация",
        FactKey.AFFECTED_SERVICES: ["Имплантация"],
        FactKey.PATIENT_DEMAND: ["REFUND"],
    })
    assert queries == ("договор", "согласие", "гарант")
