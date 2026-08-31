from datetime import date
from uuid import UUID

from agent_orchestrator.projection import build_case_projection
from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment


FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000001")


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


def test_projection_redacts_direct_identifiers_inside_nested_facts() -> None:
    projection = build_case_projection(
        case_id=UUID("00000000-0000-0000-0000-000000000010"),
        as_of_date=date(2026, 8, 31),
        facts={
            FactKey.PROBLEM_SUMMARY: "Иванов Иван: +7 999 123-45-67, скол винира",
            FactKey.CLINIC_DOCUMENTS: {"CONTRACT": "AVAILABLE"},
        },
        evidence=[_evidence()],
        known_identifiers={"Иванов Иван": "[PATIENT_1]"},
    )

    summary = projection.facts[FactKey.PROBLEM_SUMMARY.value]
    assert summary == "[PATIENT_1]: [PHONE], скол винира"
    assert projection.evidence[0].fragment_id == FRAGMENT_ID
