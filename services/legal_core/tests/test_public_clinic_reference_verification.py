import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parents[3]
VERIFICATION = (
    ROOT / "services/legal_core/corpus/public_clinic_reference_verification.v1.json"
)


def test_live_public_reference_verification_is_metadata_only_and_sufficient() -> None:
    payload = json.loads(VERIFICATION.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "dental-public-clinic-reference-verification.v1"
    assert payload["verified_at"] == "2026-09-01"
    assert payload["purpose"] == "DEVELOPMENT_REFERENCE_SOURCE_LIVENESS_ONLY"
    assert payload["authority"] == "NOT_A_LEGAL_SOURCE"
    assert payload["automatic_ingestion"] is False

    checked = payload["checked_sources"]
    assert 15 <= len(checked) <= 20
    assert len({item["clinic_key"] for item in checked}) == len(checked)

    coverage: set[str] = set()
    for item in checked:
        assert item["status"] in {"LIVE_VERIFIED", "LIVE_VERIFIED_SEARCH"}
        parsed = urlparse(item["url"])
        assert parsed.scheme == "https"
        assert parsed.hostname
        assert parsed.username is None
        assert parsed.password is None
        assert item["coverage"]
        coverage.update(item["coverage"])

    assert {
        "CONTRACT",
        "WARRANTY_POLICY",
        "PATIENT_RULES",
        "INFORMED_CONSENT_GENERAL",
        "INFORMED_CONSENT_SURGERY",
        "INFORMED_CONSENT_PROSTHODONTICS",
        "INFORMED_CONSENT_ORTHODONTICS",
        "MEDICAL_RECORD_ACCESS_POLICY",
    } <= coverage

    excluded = payload["excluded_or_recheck"]
    assert any(item["clinic_key"] == "beleira" for item in excluded)

    replacements = payload["replacement_candidates"]
    assert len(replacements) >= 2
    for item in replacements:
        parsed = urlparse(item["url"])
        assert parsed.scheme == "https"
        assert parsed.hostname
        assert item["coverage"]
        assert item["research_value"]
