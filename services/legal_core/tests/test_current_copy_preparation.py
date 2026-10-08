import hashlib
from datetime import date

from legal_core.material_preparation import MaterialPreparationInput


def payload():
    text = "Synthetic article text retained for a human-reviewed current copy."
    return {
        "raw_sha256": "a" * 64,
        "title": "Synthetic copy",
        "kind": "NORMATIVE",
        "group_key": "courts",
        "parser_version": "synthetic.v1",
        "extraction_scope": "PARTIAL",
        "normalized_text": text,
        "normalized_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "limitations": ["Only the text layer is prepared; graphics remain in the original."],
        "parts": [
            {
                "part_key": "document",
                "title": "Synthetic copy",
                "date_basis": "LAWYER_CURRENT_COPY",
                "copy_valid_from": "2026-10-08",
                "text_start": 0,
                "text_end": len(text),
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "evidence": {"copy_valid_from": "Owner-requested current-copy review snapshot"},
            }
        ],
    }


def test_scoped_current_copy_does_not_claim_full_extraction_or_statutory_dates():
    actual = MaterialPreparationInput.model_validate(payload())
    assert actual.extraction_scope == "PARTIAL" and actual.limitations
    assert actual.parts[0].effective_from is None
    assert actual.parts[0].copy_valid_from == date(2026, 10, 8)


def test_legacy_preparation_digest_and_metadata_remain_compatible():
    data = payload()
    part = data["parts"][0]
    for field in ["date_basis", "copy_valid_from", "text_start", "text_end", "text_sha256"]:
        part.pop(field)
    part["evidence"] = {}
    actual = MaterialPreparationInput.model_validate(data)
    metadata = actual.metadata()
    assert "date_basis" not in metadata["parts"][0]
    assert "copy_valid_from" not in metadata["parts"][0]
