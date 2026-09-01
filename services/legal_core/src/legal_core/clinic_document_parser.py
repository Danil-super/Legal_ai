"""Safe local parsing of tenant-owned clinic document uploads.

The parser accepts only a narrow MVP set (TXT, PDF, DOCX), computes the raw SHA-256 itself and
extracts text locally. It never treats client-provided hashes or extracted text as authoritative.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree

from legal_core.clinic_documents import normalize_clinic_document_text

MAX_UPLOAD_BYTES = 15_000_000
MAX_EXTRACTED_TEXT_CHARS = 200_000
MAX_PDF_PAGES = 100
MAX_DOCX_ENTRIES = 2_000
MAX_DOCX_UNCOMPRESSED_BYTES = 30_000_000
MAX_DOCX_SINGLE_ENTRY_BYTES = 12_000_000
MAX_DOCX_COMPRESSION_RATIO = 200

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIME = "text/plain"
SUPPORTED_MIME_TYPES = frozenset({PDF_MIME, DOCX_MIME, TEXT_MIME})

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True, slots=True)
class ParsedClinicDocumentUpload:
    source_filename: str
    mime_type: str
    raw_sha256: str
    raw_size_bytes: int
    normalized_text: str
    parser_version: str


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_filename(value: str) -> str:
    filename = value.strip()
    if not filename or len(filename) > 255:
        raise ValueError("source filename must contain 1 to 255 characters")
    if filename in {".", ".."} or "/" in filename or "\\" in filename or "\x00" in filename:
        raise ValueError("source filename must not contain a path")
    return filename


def _bounded_text(value: str) -> str:
    normalized = normalize_clinic_document_text(value)
    if len(normalized) > MAX_EXTRACTED_TEXT_CHARS:
        raise ValueError("clinic document extracted text exceeds the supported size")
    return normalized


def _run_tool(
    arguments: Sequence[str],
    *,
    timeout_seconds: int = 30,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    try:
        return subprocess.run(
            list(arguments),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
            env=environment,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"required document parser is not installed: {arguments[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"document parser timed out: {arguments[0]}") from exc
    except subprocess.CalledProcessError as exc:
        diagnostic = exc.stderr.strip()[:300]
        raise ValueError(f"document parser rejected the file: {diagnostic}") from exc


def _parse_pdf(raw: bytes) -> tuple[str, str]:
    if not raw.startswith(b"%PDF-"):
        raise ValueError("PDF has an invalid signature")
    with tempfile.NamedTemporaryFile(prefix="clinic-document-", suffix=".pdf") as temporary:
        temporary.write(raw)
        temporary.flush()
        info = _run_tool(["pdfinfo", temporary.name]).stdout
        pages_match = re.search(r"(?m)^Pages:\s+(\d+)\s*$", info)
        encrypted_match = re.search(r"(?m)^Encrypted:\s+(\S+)\s*$", info)
        if pages_match is None:
            raise ValueError("pdfinfo did not report a page count")
        if encrypted_match is not None and encrypted_match.group(1).lower() != "no":
            raise ValueError("encrypted clinic PDFs are not supported")
        page_count = int(pages_match.group(1))
        if not 1 <= page_count <= MAX_PDF_PAGES:
            raise ValueError("clinic PDF page count exceeds the supported limit")
        extracted = _run_tool(
            ["pdftotext", "-enc", "UTF-8", "-nopgbrk", temporary.name, "-"],
            timeout_seconds=60,
        ).stdout
    try:
        normalized = _bounded_text(extracted)
    except ValueError as exc:
        if "must not be empty" in str(exc):
            raise ValueError(
                "clinic PDF has no extractable text; scanned PDFs require a separate OCR flow"
            ) from exc
        raise
    return normalized, "pdftotext-clinic.v1"


def _validate_docx_archive(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_DOCX_ENTRIES:
        raise ValueError("DOCX archive contains an unsupported number of entries")

    total_uncompressed = 0
    names: set[str] = set()
    for info in infos:
        name = info.filename.replace("\\", "/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("DOCX archive contains an unsafe path")
        names.add(name)
        if info.file_size > MAX_DOCX_SINGLE_ENTRY_BYTES:
            raise ValueError("DOCX archive entry exceeds the supported size")
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise ValueError("DOCX archive expands beyond the supported size")
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_DOCX_COMPRESSION_RATIO:
            raise ValueError("DOCX archive compression ratio is unsafe")

    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise ValueError("DOCX archive is missing required Office XML parts")
    lowered = {name.casefold() for name in names}
    if any(name.endswith("vbaproject.bin") for name in lowered):
        raise ValueError("macro-enabled Office documents are not supported")
    if any(name.startswith("word/embeddings/") for name in lowered):
        raise ValueError("embedded objects in DOCX are not supported")


def _docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    pieces: list[str] = []
    for element in paragraph.iter():
        if element.tag == f"{_WORD_NS}t":
            pieces.append(element.text or "")
        elif element.tag == f"{_WORD_NS}tab":
            pieces.append("\t")
        elif element.tag in {f"{_WORD_NS}br", f"{_WORD_NS}cr"}:
            pieces.append("\n")
    return "".join(pieces).strip()


def _parse_docx(raw: bytes) -> tuple[str, str]:
    try:
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(raw)) as archive:
            _validate_docx_archive(archive)
            document_xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("DOCX archive is invalid") from exc

    upper_xml = document_xml.upper()
    if b"<!DOCTYPE" in upper_xml or b"<!ENTITY" in upper_xml:
        raise ValueError("DOCX XML declarations are unsafe")
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise ValueError("DOCX document XML is invalid") from exc

    paragraphs = [
        text
        for paragraph in root.iter(f"{_WORD_NS}p")
        if (text := _docx_paragraph_text(paragraph))
    ]
    if not paragraphs:
        raise ValueError("DOCX contains no extractable text")
    return _bounded_text("\n\n".join(paragraphs)), "docx-wordprocessingml.v1"


def _parse_text(raw: bytes) -> tuple[str, str]:
    try:
        decoded = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("plain-text clinic documents must be UTF-8") from exc
    return _bounded_text(decoded), "utf8-text.v1"


def _resolve_kind(filename: str, content_type: str) -> str:
    normalized_content_type = content_type.split(";", 1)[0].strip().lower()
    extension = PurePosixPath(filename).suffix.casefold()
    extension_mime = {".pdf": PDF_MIME, ".docx": DOCX_MIME, ".txt": TEXT_MIME}.get(extension)
    if extension_mime is None:
        raise ValueError("only .pdf, .docx and .txt clinic documents are supported")
    if normalized_content_type in {"", "application/octet-stream"}:
        return extension_mime
    if normalized_content_type not in SUPPORTED_MIME_TYPES:
        raise ValueError("clinic document content type is not supported")
    if normalized_content_type != extension_mime:
        raise ValueError("clinic document filename and content type do not match")
    return normalized_content_type


def parse_clinic_document_upload(
    raw: bytes,
    *,
    source_filename: str,
    content_type: str,
) -> ParsedClinicDocumentUpload:
    filename = _safe_filename(source_filename)
    if not raw:
        raise ValueError("clinic document upload must not be empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("clinic document upload exceeds the supported size")

    mime_type = _resolve_kind(filename, content_type)
    if mime_type == PDF_MIME:
        normalized_text, parser_version = _parse_pdf(raw)
    elif mime_type == DOCX_MIME:
        normalized_text, parser_version = _parse_docx(raw)
    else:
        normalized_text, parser_version = _parse_text(raw)

    return ParsedClinicDocumentUpload(
        source_filename=filename,
        mime_type=mime_type,
        raw_sha256=sha256_bytes(raw),
        raw_size_bytes=len(raw),
        normalized_text=normalized_text,
        parser_version=parser_version,
    )
