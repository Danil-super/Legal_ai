"""Small, synthetic attack inputs: no real patients, secrets or external requests."""

from __future__ import annotations

import io
import struct
import subprocess
import warnings
import zipfile

import pytest

from legal_core import clinic_document_parser as parser

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx(xml: bytes, extra: tuple[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w") as archive:
        warnings.simplefilter("ignore", UserWarning)
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
        if extra:
            archive.writestr(*extra)
    return buffer.getvalue()


def xml_document(text: str = "Synthetic text") -> str:
    return f'<w:document xmlns:w="{NS}"><w:body><w:p><w:r><w:t>{text}</w:t>' \
        '</w:r></w:p></w:body></w:document>'


def parse(raw: bytes) -> parser.ParsedClinicDocumentUpload:
    return parser.parse_clinic_document_upload(
        raw, source_filename="synthetic.docx", content_type=parser.DOCX_MIME,
    )


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be"])
@pytest.mark.parametrize("external", [False, True])
def test_dtd_is_rejected_independently_of_xml_encoding(encoding: str, external: bool) -> None:
    declaration = (
        '<!DOCTYPE w:document SYSTEM "file:///synthetic-never-read">'
        if external else '<!DOCTYPE w:document [<!ENTITY demo "synthetic entity">]>'
    )
    text = "Synthetic text" if external else "&demo;"
    declared = "UTF-16" if encoding.startswith("utf-16") else "UTF-8"
    xml = f'<?xml version="1.0" encoding="{declared}"?>{declaration}{xml_document(text)}'
    with pytest.raises(ValueError, match="unsafe"):
        parse(docx(xml.encode(encoding)))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be"])
def test_valid_unicode_docx_still_parses(encoding: str) -> None:
    declared = "UTF-16" if encoding.startswith("utf-16") else "UTF-8"
    xml = f'<?xml version="1.0" encoding="{declared}"?>{xml_document("Синтетический текст")}'
    result = parse(docx(xml.encode(encoding)))
    assert result.normalized_text == "Синтетический текст"
    assert len(result.raw_sha256) == 64


@pytest.mark.parametrize("part", ["word/document.xml", "word/./document.xml", "word\\document.xml"])
def test_duplicate_or_ambiguous_archive_names_are_rejected(part: str) -> None:
    raw = docx(xml_document().encode(), (part, xml_document("Different text").encode()))
    with pytest.raises(ValueError, match="duplicate|unsafe"):
        parse(raw)


def test_encrypted_docx_is_validation_error_not_missing_parser() -> None:
    raw = bytearray(docx(xml_document().encode()))
    # Set the encryption flag in both headers; no encryption implementation is needed.
    offset = 0
    while (offset := raw.find(b"PK\x01\x02", offset)) >= 0:
        struct.pack_into("<H", raw, offset + 8, 1)
        offset += 4
    offset = 0
    while (offset := raw.find(b"PK\x03\x04", offset)) >= 0:
        struct.pack_into("<H", raw, offset + 6, 1)
        offset += 4
    with pytest.raises(ValueError, match="encrypted"):
        parse(bytes(raw))


@pytest.mark.parametrize("name", [
    "word/../document.xml", "/word/document.xml", "word//document.xml",
])
def test_unsafe_archive_paths_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="unsafe|duplicate"):
        parse(docx(xml_document().encode(), (name, b"unused")))


@pytest.mark.parametrize("name", [
    "bad\nname.txt", "bad\rname.txt", "bad\tname.txt", "bad\x7fname.txt",
])
def test_filename_control_characters_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="filename"):
        parser.parse_clinic_document_upload(
            b"synthetic text", source_filename=name, content_type=parser.TEXT_MIME,
        )


def test_parser_stderr_is_not_reflected_in_application_errors(monkeypatch) -> None:
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1, ["pdfinfo", "synthetic.pdf"], stderr="SYNTHETIC_PRIVATE_TEXT",
        )

    monkeypatch.setattr(parser.subprocess, "run", failed)
    with pytest.raises(ValueError) as error:
        parser._run_tool(["pdfinfo", "synthetic.pdf"])
    assert "SYNTHETIC_PRIVATE_TEXT" not in str(error.value)
    assert str(error.value) == "document parser rejected the file"


def test_oversized_docx_part_is_rejected_before_reading(monkeypatch) -> None:
    monkeypatch.setattr(parser, "MAX_DOCX_SINGLE_ENTRY_BYTES", 32)
    with pytest.raises(ValueError, match="size"):
        parse(docx(xml_document().encode()))
