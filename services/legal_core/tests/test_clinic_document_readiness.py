from datetime import date
from uuid import UUID

from legal_core.clinic_document_readiness import (
    ClinicDocumentReadinessStatus,
    assess_clinic_document_readiness,
    plan_clinic_document_expectations,
)
from legal_core.clinic_document_retrieval import (
    ApprovedClinicDocumentFragment,
    AvailableClinicDocument,
)
from legal_core.contracts import FactKey


def _available(
    key: str,
    document_type: str,
    *,
    ordinal: int,
) -> AvailableClinicDocument:
    return AvailableClinicDocument(
        document_id=UUID(int=100 + ordinal),
        version_id=UUID(int=200 + ordinal),
        document_key=key,
        document_type=document_type,
        document_title=key,
        version_no=1,
        valid_from=date(2026, 1, 1),
        valid_to=None,
    )


def _fragment(
    key: str,
    document_type: str,
    *,
    ordinal: int,
) -> ApprovedClinicDocumentFragment:
    document = _available(key, document_type, ordinal=ordinal)
    return ApprovedClinicDocumentFragment(
        fragment_id=UUID(int=300 + ordinal),
        version_id=document.version_id,
        document_id=document.document_id,
        document_key=document.document_key,
        document_type=document.document_type,
        document_title=document.document_title,
        version_no=document.version_no,
        valid_from=document.valid_from,
        valid_to=document.valid_to,
        ordinal=1,
        structural_path="text/fragment/1",
        fragment_text="Synthetic fragment",
        text_sha256="a" * 64,
        raw_sha256="b" * 64,
    )


def test_implant_case_plans_core_and_scenario_specific_context() -> None:
    expectations = plan_clinic_document_expectations(
        {
            FactKey.SERVICE_TYPE: "Имплантация зуба",
            FactKey.INCIDENT_TYPES: ["IMPLANT_PROBLEM"],
            FactKey.PATIENT_DEMAND: ["REMAKE"],
        }
    )

    assert [item.expectation_code for item in expectations] == [
        "CONTRACT",
        "GENERAL_CONSENT",
        "WARRANTY_POLICY",
        "IMPLANT_CONSENT",
        "POST_IMPLANT_MEMO",
    ]
    implant = next(item for item in expectations if item.expectation_code == "IMPLANT_CONSENT")
    assert "INFORMED_CONSENT_IMPLANT" in implant.accepted_document_types
    assert "INFORMED_CONSENT_SURGERY" in implant.accepted_document_types


def test_formal_records_case_adds_record_access_and_claim_workflow() -> None:
    expectations = plan_clinic_document_expectations(
        {
            FactKey.SERVICE_TYPE: "Терапевтическое лечение",
            FactKey.INCIDENT_TYPES: ["RECORDS_REQUEST", "FORMAL_CLAIM"],
            FactKey.PATIENT_DEMAND: ["DOCUMENTS"],
            FactKey.FORMAL_CLAIM: True,
        }
    )

    assert {item.expectation_code for item in expectations} == {
        "CONTRACT",
        "GENERAL_CONSENT",
        "MEDICAL_RECORD_ACCESS",
        "CLAIM_WORKFLOW",
    }


def test_readiness_distinguishes_retrieved_available_and_absent_documents() -> None:
    expectations = plan_clinic_document_expectations(
        {
            FactKey.SERVICE_TYPE: "Установка коронки",
            FactKey.INCIDENT_TYPES: ["CROWN_PROBLEM"],
            FactKey.PATIENT_DEMAND: ["REFUND"],
        }
    )
    available = [
        _available("contract-main", "CONTRACT", ordinal=1),
        _available("consent-general", "INFORMED_CONSENT_GENERAL", ordinal=2),
    ]
    retrieved = [_fragment("contract-main", "CONTRACT", ordinal=1)]

    readiness = assess_clinic_document_readiness(
        expectations,
        available_documents=available,
        retrieved_fragments=retrieved,
    )
    by_code = {item.expectation_code: item for item in readiness}

    assert by_code["CONTRACT"].status is ClinicDocumentReadinessStatus.RETRIEVED
    assert by_code["CONTRACT"].matched_document_keys == ("contract-main",)
    assert (
        by_code["GENERAL_CONSENT"].status
        is ClinicDocumentReadinessStatus.AVAILABLE_NOT_RETRIEVED
    )
    assert by_code["GENERAL_CONSENT"].matched_document_keys == ("consent-general",)
    assert by_code["WARRANTY_POLICY"].status is ClinicDocumentReadinessStatus.NOT_AVAILABLE
    assert by_code["WARRANTY_POLICY"].matched_document_keys == ()


def test_specialty_consent_alias_satisfies_implant_expectation() -> None:
    expectations = plan_clinic_document_expectations(
        {
            FactKey.SERVICE_TYPE: "Имплантация",
            FactKey.INCIDENT_TYPES: ["IMPLANT_PROBLEM"],
        }
    )
    specialty = _available("surgery-consent", "INFORMED_CONSENT_SURGERY", ordinal=3)

    readiness = assess_clinic_document_readiness(
        expectations,
        available_documents=[specialty],
        retrieved_fragments=[],
    )
    implant = next(item for item in readiness if item.expectation_code == "IMPLANT_CONSENT")

    assert implant.status is ClinicDocumentReadinessStatus.AVAILABLE_NOT_RETRIEVED
    assert implant.matched_document_keys == ("surgery-consent",)
