from datetime import date

from legal_core.clinic_documents import prepare_clinic_document_text
from legal_core.synthetic_clinic_fixtures import (
    load_synthetic_clinic_versions,
    synthetic_version_at,
)


def test_synthetic_clinic_fixture_pack_is_complete_and_hash_locked() -> None:
    versions = load_synthetic_clinic_versions()

    assert len(versions) == 9
    assert len({item.filename for item in versions}) == 9
    assert len({item.sha256 for item in versions}) == 9
    assert {
        "CONTRACT",
        "WARRANTY_POLICY",
        "INFORMED_CONSENT_GENERAL",
        "INFORMED_CONSENT_IMPLANT",
        "PATIENT_RULES",
        "MEDICAL_RECORD_ACCESS_POLICY",
        "CLAIM_POLICY",
        "PATIENT_MEMO_IMPLANT",
    } <= {item.document_type for item in versions}

    for item in versions:
        assert item.text.startswith("СИНТЕТИЧ")
        assert "https://" not in item.text
        assert "http://" not in item.text
        prepared = prepare_clinic_document_text(item.text)
        assert prepared.content_sha256 == item.sha256
        assert prepared.fragments


def test_synthetic_contract_versions_form_a_half_open_time_line() -> None:
    before = synthetic_version_at("service-contract", as_of_date=date(2026, 6, 30))
    boundary = synthetic_version_at("service-contract", as_of_date=date(2026, 7, 1))
    after = synthetic_version_at("service-contract", as_of_date=date(2026, 9, 1))

    assert before.version_no == 1
    assert before.valid_to == date(2026, 7, 1)
    assert boundary.version_no == 2
    assert after.version_no == 2
    assert boundary.valid_from == date(2026, 7, 1)


def test_synthetic_pack_contains_operational_queries_needed_by_case_context() -> None:
    combined = "\n".join(item.text.casefold() for item in load_synthetic_clinic_versions())

    for token in (
        "договор",
        "соглас",
        "гарант",
        "претенз",
        "возврат",
        "документ",
        "имплант",
    ):
        assert token in combined
