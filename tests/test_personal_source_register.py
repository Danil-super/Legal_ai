"""Review metadata must never masquerade as runtime-approved legal evidence."""

import json
from pathlib import Path
from urllib.parse import urlparse

from legal_core.personal.catalog import CATALOG

ROOT = Path(__file__).resolve().parents[1]


def test_personal_register_is_explicitly_non_production_and_covers_catalog():
    register = json.loads((ROOT / "docs/personal/legal-source-register.v1.json").read_text())
    assert register["register_version"] == "personal-source-review.v1"
    assert register["production_ingestion_allowed"] is False
    identifiers = set()
    for entry in register["sources"]:
        assert entry["source_id"] not in identifiers
        identifiers.add(entry["source_id"])
        url = urlparse(entry["official_url"])
        assert url.scheme == "https"
        assert url.hostname in {"pravo.gov.ru", "publication.pravo.gov.ru"}
        assert url.username is None and url.password is None
        assert entry["status"] == "REVIEW_REQUIRED"
        assert entry["scope"] == "DISCOVERY_ONLY"
        assert entry["approved_for_personal_runtime"] is False
        assert entry["applicable_edition_verified"] is False
        assert entry["raw_sha256"] is None  # No invented digest for an unfetched artifact.
        assert entry["observation"] and entry["review_required"]
    assert len(identifiers) == 7
    for card in CATALOG:
        assert set(card.source_candidates) <= identifiers
