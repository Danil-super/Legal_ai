import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from legal_core.review_materials import collect_review_materials, review_material_from_path
from legal_core.models import LegalReviewMaterial
from legal_core.api_contracts import LegalEditorReviewMaterialPage


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


def test_review_material_quarantines_an_ambiguous_garant_rtf_for_editor_verification(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "ambiguous.rtf"
    artifact.write_bytes(
        b"{\\rtf1\\ansi https://internet.garant.ru/document/redirect/12191967/0 "
        b"https://internet.garant.ru/document/redirect/12191968/0}"
    )

    material = review_material_from_path(
        artifact, received_at=datetime(2026, 9, 25, 12, tzinfo=UTC)
    )

    assert material.kind == "LEGAL_COPY"
    assert material.review_state == "METADATA_REQUIRED"
    assert material.source_url is None
    assert material.source_external_id is None
    assert "сверка" in material.source_name


def test_collect_review_materials_is_sorted_and_rejects_symlinks(tmp_path: Path) -> None:
    (tmp_path / "z.pdf").write_bytes(b"%PDF-1.7\\nclinical recommendation\\n%%EOF\\n")
    (tmp_path / "a.rtf").write_bytes(
        b"{\\rtf1\\ansi https://internet.garant.ru/document/redirect/12191967/0}"
    )
    (tmp_path / "outside.rtf").symlink_to(tmp_path / "a.rtf")

    with pytest.raises(ValueError, match="regular file"):
        collect_review_materials(tmp_path, received_at=datetime(2026, 9, 25, tzinfo=UTC))

    (tmp_path / "outside.rtf").unlink()
    materials = collect_review_materials(tmp_path, received_at=datetime(2026, 9, 25, tzinfo=UTC))

    assert [material.original_filename for material in materials] == ["a.rtf", "z.pdf"]


def test_review_materials_have_a_separate_non_retrievable_persistence_table() -> None:
    table = LegalReviewMaterial.__table__

    assert table.name == "legal_review_materials"
    assert {column.name for column in table.columns} >= {
        "package_key",
        "kind",
        "review_state",
        "raw_sha256",
        "raw_bytes",
    }


def test_review_material_api_contract_keeps_the_file_identity_without_approval_state() -> None:
    payload = LegalEditorReviewMaterialPage.model_validate(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 1,
            "items": [
                {
                    "materialId": "00000000-0000-0000-0000-000000000001",
                    "packageKey": "garant-lawyer-2026-09-25",
                    "title": "323-ФЗ",
                    "kind": "LEGAL_COPY",
                    "reviewState": "METADATA_REQUIRED",
                    "sourceName": "Гарант",
                    "sourceUrl": "https://internet.garant.ru/document/redirect/12191967/0",
                    "rawMimeType": "application/rtf",
                    "rawSizeBytes": 123,
                    "rawSha256": "a" * 64,
                    "receivedAt": "2026-09-25T12:00:00Z",
                }
            ],
        }
    )

    assert payload.items[0].review_state == "METADATA_REQUIRED"
