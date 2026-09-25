import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from legal_core.review_materials import review_material_from_path


def test_review_material_preserves_a_garant_rtf_as_a_non_approved_legal_copy(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "323-ФЗ.rtf"
    raw = (
        "{\\rtf1\\ansi Федеральный закон\\line "
        "https://internet.garant.ru/document/redirect/12191967/0}"
    ).encode()
    artifact.write_bytes(raw)

    material = review_material_from_path(
        artifact, received_at=datetime(2026, 9, 25, 12, tzinfo=UTC)
    )

    assert material.kind == "LEGAL_COPY"
    assert material.review_state == "METADATA_REQUIRED"
    assert material.source_name == "Гарант"
    assert material.source_url == "https://internet.garant.ru/document/redirect/12191967/0"
    assert material.mime_type == "application/rtf"
    assert material.raw_sha256 == hashlib.sha256(raw).hexdigest()


def test_review_material_keeps_clinical_pdf_out_of_legal_corpus(tmp_path: Path) -> None:
    artifact = tmp_path / "КР1015_1.pdf"
    raw = b"%PDF-1.7\\nclinical recommendation\\n%%EOF\\n"
    artifact.write_bytes(raw)

    material = review_material_from_path(
        artifact, received_at=datetime(2026, 9, 25, 12, tzinfo=UTC)
    )

    assert material.kind == "CLINICAL_REFERENCE"
    assert material.review_state == "METADATA_REQUIRED"
    assert material.source_name == "Передано юристом; первоисточник не зафиксирован"
    assert material.source_url is None
    assert material.mime_type == "application/pdf"


def test_review_material_rejects_rtf_with_embedded_object(tmp_path: Path) -> None:
    artifact = tmp_path / "unsafe.rtf"
    artifact.write_bytes(b"{\\rtf1\\ansi\\object\\objdata unsafe}")

    with pytest.raises(ValueError, match="unsafe"):
        review_material_from_path(artifact, received_at=datetime(2026, 9, 25, tzinfo=UTC))
