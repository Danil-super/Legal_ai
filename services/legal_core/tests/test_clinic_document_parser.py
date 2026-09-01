from __future__ import annotations

import io
import zipfile

import pytest

from legal_core import clinic_document_parser as parser


def _docx_bytes(
    *,
    paragraphs: tuple[str, ...] = ("Договор оказания услуг.", "Гарантийный срок указан ниже."),
    extra_entries: dict[str, bytes] | None = None,
) -> bytes:
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="xml" ContentType="application/xml"/>
</Types>"""
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    document = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{body}</w:body></w:document>"
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
        for name, value in (extra_entries or {}).items():
            archive.writestr(name, value)
    return output.getvalue()


def test_plain_text_upload_is_server_hashed_and_normalized() -> None:
    raw = "\ufeffДоговор\r\n\r\nГарантия действует 30 дней.\r\n".encode("utf-8")

    parsed = parser.parse_clinic_document_upload(
        raw,
        source_filename="contract.txt",
        content_type="text/plain; charset=utf-8",
    )

    assert parsed.mime_type == parser.TEXT_MIME
    assert parsed.raw_sha256 == parser.sha256_bytes(raw)
    assert parsed.raw_size_bytes == len(raw)
    assert parsed.normalized_text == "Договор\n\nГарантия действует 30 дней."
    assert parsed.parser_version == "utf8-text.v1"


def test_docx_upload_extracts_wordprocessingml_paragraphs() -> None:
    raw = _docx_bytes()

    parsed = parser.parse_clinic_document_upload(
        raw,
        source_filename="warranty.docx",
        content_type=parser.DOCX_MIME,
    )

    assert parsed.mime_type == parser.DOCX_MIME
    assert parsed.normalized_text == (
        "Договор оказания услуг.\n\nГарантийный срок указан ниже."
    )
    assert parsed.parser_version == "docx-wordprocessingml.v1"


def test_docx_rejects_path_traversal_and_macros() -> None:
    with pytest.raises(ValueError, match="unsafe path"):
        parser.parse_clinic_document_upload(
            _docx_bytes(extra_entries={"../outside.xml": b"x"}),
            source_filename="unsafe.docx",
            content_type=parser.DOCX_MIME,
        )

    with pytest.raises(ValueError, match="macro-enabled"):
        parser.parse_clinic_document_upload(
            _docx_bytes(extra_entries={"word/vbaProject.bin": b"macro"}),
            source_filename="macro.docx",
            content_type=parser.DOCX_MIME,
        )


def test_upload_rejects_filename_mime_mismatch_and_paths() -> None:
    with pytest.raises(ValueError, match="do not match"):
        parser.parse_clinic_document_upload(
            b"plain text",
            source_filename="contract.txt",
            content_type=parser.PDF_MIME,
        )

    with pytest.raises(ValueError, match="must not contain a path"):
        parser.parse_clinic_document_upload(
            b"plain text",
            source_filename="../contract.txt",
            content_type=parser.TEXT_MIME,
        )


def test_pdf_requires_real_signature_before_external_tool() -> None:
    with pytest.raises(ValueError, match="invalid signature"):
        parser.parse_clinic_document_upload(
            b"not a pdf",
            source_filename="contract.pdf",
            content_type=parser.PDF_MIME,
        )


def test_pdf_dispatch_keeps_raw_hash_server_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"%PDF-1.7\nsynthetic"

    monkeypatch.setattr(
        parser,
        "_parse_pdf",
        lambda value: ("Извлеченный текст договора.", "synthetic-pdf-parser.v1"),
    )

    parsed = parser.parse_clinic_document_upload(
        raw,
        source_filename="contract.pdf",
        content_type="application/octet-stream",
    )

    assert parsed.raw_sha256 == parser.sha256_bytes(raw)
    assert parsed.normalized_text == "Извлеченный текст договора."
    assert parsed.parser_version == "synthetic-pdf-parser.v1"
