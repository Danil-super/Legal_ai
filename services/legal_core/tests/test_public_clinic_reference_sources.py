import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parents[3]
REGISTRY = ROOT / "services/legal_core/corpus/public_clinic_reference_sources.v1.json"


def test_public_clinic_reference_registry_is_metadata_only_and_bounded() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "dental-public-clinic-reference.v1"
    assert payload["purpose"] == "DEVELOPMENT_REFERENCE_ONLY"
    assert payload["authority"] == "NOT_A_LEGAL_SOURCE"
    assert payload["ingestion_policy"] == "METADATA_ONLY_NO_AUTOMATIC_RAG_INGESTION"
    assert payload["raw_document_policy"].startswith("DO_NOT_MIRROR_OR_REPUBLISH")

    clinics = payload["clinics"]
    assert 15 <= len(clinics) <= 20

    clinic_keys = [clinic["clinic_key"] for clinic in clinics]
    assert len(clinic_keys) == len(set(clinic_keys))

    urls: list[str] = []
    document_types: set[str] = set()
    for clinic in clinics:
        assert clinic["clinic_name"].strip()
        assert clinic["source_pages"]
        assert clinic["research_value"]
        for page in clinic["source_pages"]:
            parsed = urlparse(page["url"])
            assert parsed.scheme == "https"
            assert parsed.hostname
            assert parsed.username is None
            assert parsed.password is None
            assert not parsed.fragment
            assert page["observed_document_types"]
            urls.append(page["url"])
            document_types.update(page["observed_document_types"])

    assert len(urls) == len(set(urls))
    assert {
        "CONTRACT",
        "WARRANTY_POLICY",
        "PATIENT_RULES",
        "INFORMED_CONSENT_GENERAL",
        "INFORMED_CONSENT_SURGERY",
        "INFORMED_CONSENT_PROSTHODONTICS",
        "INFORMED_CONSENT_ORTHODONTICS",
        "PATIENT_MEMO",
        "MEDICAL_RECORD_ACCESS_POLICY",
    } <= document_types
