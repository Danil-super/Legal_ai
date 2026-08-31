"""Deterministic preparation of clinic-owned text documents for reviewed retrieval."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MAX_FRAGMENT_CHARS = 1_200


@dataclass(frozen=True, slots=True)
class ClinicDocumentFragmentInput:
    ordinal: int
    structural_path: str
    fragment_text: str
    text_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedClinicDocumentText:
    normalized_text: str
    content_sha256: str
    fragments: tuple[ClinicDocumentFragmentInput, ...]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_clinic_document_text(value: str) -> str:
    """Normalize only transport whitespace; never rewrite substantive clinic wording."""

    normalized = value.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n")).strip()
    if not normalized:
        raise ValueError("clinic document text must not be empty")
    return normalized


def _split_oversized_paragraph(paragraph: str) -> list[str]:
    if len(paragraph) <= MAX_FRAGMENT_CHARS:
        return [paragraph]

    words = paragraph.split()
    if not words:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for word in words:
        additional = len(word) if not current else len(word) + 1
        if current and current_length + additional > MAX_FRAGMENT_CHARS:
            chunks.append(" ".join(current))
            current = [word]
            current_length = len(word)
            continue
        if not current and len(word) > MAX_FRAGMENT_CHARS:
            chunks.extend(
                word[offset : offset + MAX_FRAGMENT_CHARS]
                for offset in range(0, len(word), MAX_FRAGMENT_CHARS)
            )
            current = []
            current_length = 0
            continue
        current.append(word)
        current_length += additional
    if current:
        chunks.append(" ".join(current))
    return chunks


def _fragment_strings(normalized_text: str) -> list[str]:
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", normalized_text)
        if paragraph.strip()
    ]
    expanded = [
        chunk
        for paragraph in paragraphs
        for chunk in _split_oversized_paragraph(paragraph)
        if chunk
    ]
    fragments: list[str] = []
    current = ""
    for paragraph in expanded:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if current and len(candidate) > MAX_FRAGMENT_CHARS:
            fragments.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        fragments.append(current)
    return fragments


def prepare_clinic_document_text(value: str) -> PreparedClinicDocumentText:
    normalized_text = normalize_clinic_document_text(value)
    fragment_strings = _fragment_strings(normalized_text)
    if not fragment_strings:
        raise ValueError("clinic document text produced no fragments")
    fragments = tuple(
        ClinicDocumentFragmentInput(
            ordinal=index,
            structural_path=f"text/fragment/{index}",
            fragment_text=fragment,
            text_sha256=sha256_text(fragment),
        )
        for index, fragment in enumerate(fragment_strings, start=1)
    )
    return PreparedClinicDocumentText(
        normalized_text=normalized_text,
        content_sha256=sha256_text(normalized_text),
        fragments=fragments,
    )
