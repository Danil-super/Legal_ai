from datetime import date
from uuid import UUID

from agent_orchestrator.projection import build_case_projection
from legal_core.clinic_document_retrieval import ApprovedClinicDocumentFragment
from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment


FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000001")
CLINIC_FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000011")


def _evidence() -> ApprovedLegalFragment:
    return ApprovedLegalFragment(
        fragment_id=FRAGMENT_ID,
        version_id=UUID("00000000-0000-0000-0000-000000000002"),
        document_id=UUID("00000000-0000-0000-0000-000000000003"),
        article=None,
        part=None,
        point="1",
        structural_path="point:1",
        fragment_text="Синтетическая проверенная норма.",
        text_sha256="a" * 64,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        source_url="https://publication.pravo.gov.ru/synthetic",
        raw_sha256="b" * 64,
        document_title="Синтетический акт",
        issuer="Synthetic authority",
        official_number="1",
        version_date=date(2026, 1, 1),
        publication_date=date(2026, 1, 1),
    )


def _clinic_context() -> ApprovedClinicDocumentFragment:
    return ApprovedClinicDocumentFragment(
        fragment_id=CLINIC_FRAGMENT_ID,
        version_id=UUID("00000000-0000-0000-0000-000000000012"),
        document_id=UUID("00000000-0000-0000-0000-000000000013"),
        document_key="warranty-main",
        document_type="WARRANTY_POLICY",
        document_title="Гарантия для Иванов Иван",
        version_no=1,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        ordinal=1,
        structural_path="text/fragment/1",
        fragment_text="Иванов Иван может обратиться по телефону +7 999 123-45-67.",
        text_sha256="c" * 64,
        raw_sha256="d" * 64,
    )


def test_projection_redacts_direct_identifiers_inside_nested_facts_and_clinic_context() -> None:
    projection = build_case_projection(
        case_id=UUID("00000000-0000-0000-0000-000000000010"),
        as_of_date=date(2026, 8, 31),
        facts={
            FactKey.PROBLEM_SUMMARY: "Иванов Иван: +7 999 123-45-67, скол винира",
            FactKey.CLINIC_DOCUMENTS: {"CONTRACT": "AVAILABLE"},
        },
        evidence=[_evidence()],
        clinic_document_context=[_clinic_context()],
        known_identifiers={"Иванов Иван": "[PATIENT_1]"},
    )

    summary = projection.facts[FactKey.PROBLEM_SUMMARY.value]
    assert summary == "[PATIENT_1]: [PHONE], скол винира"
    assert projection.evidence[0].fragment_id == FRAGMENT_ID
    assert projection.clinic_document_context[0].document_title == "Гарантия для [PATIENT_1]"
    assert projection.clinic_document_context[0].text == (
        "[PATIENT_1] может обратиться по телефону [PHONE]."
    )
    clinic_payload = projection.clinic_document_context[0].model_dump(
        mode="json", by_alias=True
    )
    assert "fragmentId" not in clinic_payload
    assert str(CLINIC_FRAGMENT_ID) not in str(clinic_payload)