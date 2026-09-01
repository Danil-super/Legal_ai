import json
import re
from pathlib import Path

from legal_core.clinic_document_retrieval import plan_clinic_document_queries
from legal_core.clinic_documents import prepare_clinic_document_text
from legal_core.contracts import FactKey

ROOT = Path(__file__).parents[3]
FIXTURE_DIR = ROOT / "services/legal_core/fixtures/clinic_documents"
MANIFEST = FIXTURE_DIR / "synthetic_pack.v1.json"
PUBLIC_REGISTRY = ROOT / "services/legal_core/corpus/public_clinic_reference_sources.v1.json"
DISCLAIMER = "SYNTHETIC DEVELOPMENT FIXTURE — NOT A LEGAL TEMPLATE"


def _load_manifest() -> dict[str, object]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _document_texts(manifest: dict[str, object]) -> dict[str, str]:
    documents = manifest["documents"]
    assert isinstance(documents, list)
    result: dict[str, str] = {}
    for document in documents:
        assert isinstance(document, dict)
        key = document["document_key"]
        filename = document["filename"]
        assert isinstance(key, str)
        assert isinstance(filename, str)
        result[key] = (FIXTURE_DIR / filename).read_text(encoding="utf-8")
    return result


def test_synthetic_fixture_pack_is_bounded_original_and_parser_ready() -> None:
    manifest = _load_manifest()

    assert manifest["schema_version"] == "synthetic-clinic-documents.v1"
    assert manifest["purpose"] == "DEVELOPMENT_AND_REGRESSION_ONLY"
    assert manifest["authority"] == "NOT_A_LEGAL_SOURCE"
    assert manifest["tenant_policy"] == "DO_NOT_USE_AS_REAL_CLINIC_POLICY"
    assert manifest["created_from"] == "PUBLIC_DOCUMENT_TAXONOMY_ONLY_NO_COPIED_TEXT"

    documents = manifest["documents"]
    assert isinstance(documents, list)
    assert len(documents) == 8

    public_registry = json.loads(PUBLIC_REGISTRY.read_text(encoding="utf-8"))
    public_names = {
        str(item["clinic_name"]).casefold()
        for item in public_registry["clinics"]
        if isinstance(item, dict) and item.get("clinic_name")
    }

    keys: set[str] = set()
    types: set[str] = set()
    filenames: set[str] = set()
    for document in documents:
        assert isinstance(document, dict)
        key = document["document_key"]
        document_type = document["document_type"]
        filename = document["filename"]
        expected_queries = document["expected_queries"]
        assert isinstance(key, str)
        assert isinstance(document_type, str)
        assert isinstance(filename, str)
        assert isinstance(expected_queries, list)
        assert key not in keys
        assert filename not in filenames
        keys.add(key)
        types.add(document_type)
        filenames.add(filename)

        path = FIXTURE_DIR / filename
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        folded = text.casefold()
        assert text.startswith(DISCLAIMER)
        assert "тест-дент" in folded
        assert "http://" not in folded and "https://" not in folded
        assert "@" not in text
        assert re.search(r"\+7[\s()-]*\d", text) is None
        assert all(name not in folded for name in public_names)
        assert all(str(query).casefold() in folded for query in expected_queries)

        prepared = prepare_clinic_document_text(text)
        assert prepared.normalized_text
        assert prepared.fragments
        assert all(fragment.fragment_text for fragment in prepared.fragments)
        assert prepare_clinic_document_text(text) == prepared

    assert {
        "CONTRACT",
        "WARRANTY_POLICY",
        "INFORMED_CONSENT_GENERAL",
        "INFORMED_CONSENT_IMPLANT",
        "PATIENT_RULES",
        "MEDICAL_RECORD_ACCESS_POLICY",
        "PATIENT_MEMO_POST_IMPLANT",
        "CLAIM_POLICY",
    } == types


def _scenario_facts(scenario: dict[str, object]) -> dict[FactKey, object]:
    facts: dict[FactKey, object] = {
        FactKey.SERVICE_TYPE: scenario["service_type"],
        FactKey.INCIDENT_TYPES: scenario["incident_markers"],
        FactKey.PATIENT_DEMAND: scenario["patient_demand"],
    }
    if scenario.get("formal_claim") is True:
        facts[FactKey.FORMAL_CLAIM] = True
    return facts


def test_synthetic_regression_scenarios_are_discoverable_by_current_query_plan() -> None:
    manifest = _load_manifest()
    texts = _document_texts(manifest)
    documents = manifest["documents"]
    scenarios = manifest["regression_scenarios"]
    assert isinstance(documents, list)
    assert isinstance(scenarios, list)

    metadata = {
        str(document["document_key"]): document
        for document in documents
        if isinstance(document, dict)
    }
    assert len(scenarios) == 4

    for scenario in scenarios:
        assert isinstance(scenario, dict)
        planned = plan_clinic_document_queries(_scenario_facts(scenario))
        assert planned
        expected_keys = scenario["expected_document_keys"]
        assert isinstance(expected_keys, list)
        for expected_key in expected_keys:
            assert isinstance(expected_key, str)
            document = metadata[expected_key]
            title = str(document["title"])
            searchable = f"{expected_key}\n{title}\n{texts[expected_key]}".casefold()
            assert any(query.casefold() in searchable for query in planned), (
                scenario["scenario_id"],
                expected_key,
                planned,
            )
