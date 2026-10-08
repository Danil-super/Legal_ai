"""Synthetic current-copy contracts never invent statutory edition dates."""

import hashlib

import pytest

from legal_core.corpus_loader import CorpusFragment, CorpusManifest, corpus_fragments_sha256


def current_copy_payload():
    raw = b"{\\rtf1\\ansi Synthetic current legal copy}"
    text = "Synthetic legal article: retain the written complaint and its receipt date."
    fragment = CorpusFragment(
        ordinal=1,
        article="1",
        part=None,
        point=None,
        heading=None,
        structural_path="article:1",
        text=text,
    )
    return {
        "manifest_version": "dental-legal-corpus.v5",
        "source_key": "garant",
        "source_name": "Гарант",
        "source_trust_level": "VERIFIED_COPY",
        "source_base_url": "https://internet.garant.ru/",
        "source_url": "https://internet.garant.ru/document/redirect/11111111/0",
        "source_external_id": "11111111",
        "allowed_hosts": ["internet.garant.ru"],
        "document_key": "synthetic-current-overview",
        "document_type": "Обзор судебной практики",
        "title": "Synthetic unnumbered overview",
        "issuer": None,
        "official_number": None,
        "adoption_date": "2001-01-01",
        "publication_date": None,
        "version_date": None,
        "effective_from": "2026-10-08",
        "effective_to": None,
        "date_basis": "LAWYER_CURRENT_COPY",
        "copy_valid_from": "2026-10-08",
        "extraction_limitations": [
            "Graphics are retained in the original, not in this text layer."
        ],
        "approval_state": "REVIEW_REQUIRED",
        "artifact_kind": "THIRD_PARTY_VERIFIED_COPY",
        "artifact_mime_type": "application/rtf",
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "artifact_path": "synthetic.rtf",
        "artifact_retrieved_at": "2026-10-08T00:00:00Z",
        "artifact_size_bytes": len(raw),
        "normalized_text": text,
        "normalized_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "fragments_sha256": corpus_fragments_sha256([fragment]),
        "normalization_scope": "TEXT_LAYER",
        "parser_version": "current-copy.v1",
        "fragments": [fragment.model_dump(mode="json")],
    }


def test_current_copy_preserves_unknown_dates_and_unnumbered_identity():
    actual = CorpusManifest.model_validate(current_copy_payload())
    assert actual.version_date is None and actual.publication_date is None
    assert actual.issuer is None and actual.official_number is None
    assert actual.effective_from == actual.copy_valid_from
    assert actual.approval_state == "REVIEW_REQUIRED"
    assert actual.normalization_scope == "TEXT_LAYER"


@pytest.mark.parametrize(
    "changes",
    [
        {"copy_valid_from": None},
        {"copy_valid_from": "2026-10-07"},
        {"date_basis": "DATED_EDITION"},
        {"normalization_scope": "FULL_DOCUMENT"},
        {"extraction_limitations": []},
        {"artifact_kind": "OFFICIAL_RAW"},
        {"source_key": "other"},
    ],
)
def test_current_copy_contract_rejects_ambiguous_or_misrepresented_inputs(changes):
    with pytest.raises(ValueError):
        CorpusManifest.model_validate(current_copy_payload() | changes)


def test_current_copy_escape_hatch_is_not_available_to_legacy_manifest():
    with pytest.raises(ValueError):
        CorpusManifest.model_validate(
            current_copy_payload() | {"manifest_version": "dental-legal-corpus.v4"}
        )
