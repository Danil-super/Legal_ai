"""Inspect supported DOCX representations before anonymised material storage.

Clinic-library extraction is deliberately unchanged. This boundary cannot certify
irreversible anonymisation; it rejects identifiers and uninspectable DOCX payloads.
"""

import re
import struct
from html import unescape
from io import BytesIO
from xml.etree.ElementTree import ParseError, TreeBuilder
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
from defusedxml.ElementTree import DefusedXMLParser  # type: ignore[import-untyped]

from legal_core import clinic_document_parser as parser
from legal_core.pseudonymization import pseudonymize_text

_INSPECTABLE_PART = re.compile(
    r"(?:\[Content_Types\]\.xml|_rels/\.rels|docProps/(?:core|app|custom)\.xml|"
    r"word/(?:document|comments|commentsExtended|commentsIds|people|footnotes|endnotes|"
    r"styles|settings|webSettings|fontTable|numbering|header[0-9]+|footer[0-9]+)\.xml|"
    r"word/theme/theme[0-9]+\.xml|word/_rels/[A-Za-z0-9_.-]+\.rels)\Z"
)


class MaterialDirectIdentifier(ValueError):
    """Sanitised error: never expose the matched identifier or source part."""


def _check_text(text: str) -> None:
    if pseudonymize_text(text).changed:
        raise MaterialDirectIdentifier("Material contains a direct identifier")


def _check_comment(value: bytes) -> None:
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("Material archive metadata cannot be inspected") from None
    _check_text(text)


def _check_xml(content: bytes) -> None:
    try:
        xml_parser = DefusedXMLParser(
            target=TreeBuilder(insert_comments=True, insert_pis=True),
            forbid_dtd=True, forbid_entities=True, forbid_external=True,
        )
        xml_parser.feed(content)
        root = xml_parser.close()
    except (DefusedXmlException, ParseError, ValueError, LookupError):
        raise ValueError("Material XML cannot be inspected") from None
    # Namespace declarations (including unused ones) are not preserved as ordinary
    # attributes by ElementTree. Inspect their original decoded representation too.
    encoding = (
        "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else
        "utf-16-le" if content.startswith(b"<\x00") else
        "utf-16-be" if content.startswith(b"\x00<") else "utf-8-sig"
    )
    try:
        _check_text(unescape(content.decode(encoding, errors="strict")))
    except UnicodeDecodeError:
        raise ValueError("Material XML encoding cannot be inspected") from None
    values: list[str] = []
    for element in root.iter():
        values.extend(element.attrib.values())
        if element.text:
            values.append(element.text)
        if element.tail:
            values.append(element.tail)
    # Inspect both split words and word-separated fields, including non-visible
    # text, comments, processing instructions and attribute values.
    _check_text("".join(values))
    _check_text(" ".join(values))


def validate_anonymized_material(
    raw: bytes, parsed: parser.ParsedClinicDocumentUpload,
) -> None:
    _check_text(parsed.normalized_text)
    if parsed.mime_type != parser.DOCX_MIME:
        return
    try:
        with ZipFile(BytesIO(raw)) as archive:
            parser._validate_docx_archive(archive)
            _check_comment(archive.comment)
            end = len(raw) - 22 - len(archive.comment)
            if end < 0 or raw[end:end + 4] != b"PK\x05\x06":
                raise ValueError("Material archive has uninspectable trailing data")
            cursor = 0
            for info in sorted(archive.infolist(), key=lambda item: item.header_offset):
                if info.header_offset != cursor or raw[cursor:cursor + 4] != b"PK\x03\x04":
                    raise ValueError("Material archive contains uninspectable data")
                if cursor + 30 > len(raw):
                    raise ValueError("Material archive header is invalid")
                filename_size, local_extra_size = struct.unpack_from("<HH", raw, cursor + 26)
                local_name = raw[cursor + 30:cursor + 30 + filename_size]
                encoding = "utf-8" if info.flag_bits & 0x800 else "cp437"
                if local_name != info.filename.encode(encoding):
                    # ZipFile.open checks file names, but directory entries are not opened.
                    raise ValueError("Material archive entry names do not match")
                if info.extra or local_extra_size or info.flag_bits & 8:
                    raise ValueError("Material archive metadata cannot be inspected")
                _check_comment(info.comment)
                _check_text(info.filename)
                cursor += 30 + filename_size + info.compress_size
                if info.is_dir():
                    if info.file_size or info.compress_size:
                        raise ValueError("Material archive directory contains data")
                    continue
                if _INSPECTABLE_PART.fullmatch(info.filename) is None:
                    raise ValueError("Material contains an uninspectable DOCX part")
                with archive.open(info) as part:
                    content = part.read(parser.MAX_DOCX_SINGLE_ENTRY_BYTES + 1)
                if len(content) > parser.MAX_DOCX_SINGLE_ENTRY_BYTES:
                    raise ValueError("Material XML exceeds the supported size")
                _check_xml(content)
            if cursor != archive.start_dir:
                raise ValueError("Material archive contains uninspectable interstitial data")
    except (BadZipFile, KeyError, struct.error):
        raise ValueError("Material archive cannot be inspected") from None
