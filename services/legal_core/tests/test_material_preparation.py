import hashlib

import pytest
from pydantic import ValidationError

from legal_core.material_preparation import MaterialPreparationInput


def reference_input() -> dict:
    return {
        "raw_sha256": "a" * 64,
        "title": "Synthetic reference",
        "kind": "CLINICAL_REFERENCE",
        "group_key": "clinical",
        "parser_version": "synthetic-v1",
        "extraction_scope": "NONE",
        "limitations": ["Cover year is absent"],
    }


def test_reference_preparation_keeps_unknown_year_and_has_no_approval() -> None:
    prepared = MaterialPreparationInput.model_validate(reference_input())
    assert prepared.reference_year is None
    assert prepared.parts == []
    assert "approval_state" not in prepared.model_dump()
    assert prepared.digest() == MaterialPreparationInput.model_validate(
        prepared.model_dump()
    ).digest()


@pytest.mark.parametrize("extra", [
    {"approval_state": "APPROVED"},
    {"group_key": "healthcare"},
    {"raw_sha256": "not-a-hash"},
    {"title": "   "},
    {"kind": "LAW"},
    {"parts": [{"part_key": "part-1", "title": "Synthetic statute"}]},
    {"reference_year": True},
])
def test_invalid_reference_preparation_is_rejected(extra: dict) -> None:
    with pytest.raises(ValidationError):
        MaterialPreparationInput.model_validate(reference_input() | extra)


def test_reference_form_stays_in_healthcare_without_becoming_a_law() -> None:
    prepared = MaterialPreparationInput.model_validate(reference_input() | {
        "kind": "REFERENCE_FORM", "group_key": "healthcare",
    })
    assert prepared.kind == "REFERENCE_FORM"


def test_partial_text_must_match_hash_and_disclose_limitations() -> None:
    text = "Synthetic extracted text, not a real medical document."
    payload = reference_input() | {
        "normalized_text": text,
        "normalized_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "extraction_scope": "PARTIAL",
    }
    prepared = MaterialPreparationInput.model_validate(payload)
    assert prepared.normalized_text == text
    with pytest.raises(ValidationError):
        MaterialPreparationInput.model_validate(payload | {"normalized_sha256": "b" * 64})
    with pytest.raises(ValidationError):
        MaterialPreparationInput.model_validate(payload | {"limitations": []})
    assert prepared.digest() != MaterialPreparationInput.model_validate(
        payload | {"title": "Changed title"}
    ).digest()


def test_normative_part_retains_missing_dates_without_defaults() -> None:
    prepared = MaterialPreparationInput.model_validate(reference_input() | {
        "kind": "NORMATIVE", "group_key": "general",
        "parts": [{"part_key": "part-1", "title": "Synthetic statute"}],
    })
    assert prepared.parts[0].effective_from is None
    assert prepared.parts[0].version_date is None
    assert "effective_from" in prepared.parts[0].missing_fields()


def test_dates_need_evidence_and_duplicate_parts_are_rejected() -> None:
    part = {"part_key": "part-1", "title": "Synthetic statute"}
    payload = reference_input() | {"kind": "NORMATIVE", "group_key": "general"}
    with pytest.raises(ValidationError):
        MaterialPreparationInput.model_validate(payload | {"parts": [part, part]})
    with pytest.raises(ValidationError):
        MaterialPreparationInput.model_validate(payload | {
            "parts": [part | {"effective_from": "2026-01-01"}],
        })
    prepared = MaterialPreparationInput.model_validate(payload | {
        "parts": [part | {"effective_from": "2026-01-01", "evidence": {
            "effective_from": "Synthetic source, section 2: entry-into-force clause",
        }}],
    })
    assert prepared.parts[0].effective_from.isoformat() == "2026-01-01"


def test_preparation_never_accepts_arbitrary_fetch_urls_or_invented_full_text() -> None:
    payload = reference_input() | {"kind": "NORMATIVE", "group_key": "general"}
    with pytest.raises(ValidationError):
        MaterialPreparationInput.model_validate(payload | {
            "source_url": "http://127.0.0.1/private",
        })
    with pytest.raises(ValidationError):
        MaterialPreparationInput.model_validate(payload | {"extraction_scope": "FULL"})
