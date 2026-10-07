"""Offline, non-approving identity candidates for lawyer-supplied normative RTF copies.

The caller supplies text extracted by an offline converter. This module does not
convert RTF, fetch legal sources, create corpus versions, or establish effective law.
"""

# ruff: noqa: RUF001 -- Cyrillic legal headings and punctuation are intentional.

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from legal_core.corpus_loader import is_safe_rtf

_MAX_RAW_BYTES = 50_000_000
_MAX_TEXT_CHARS = 25_000_000
_TITLE_SCAN_BYTES = 64_000
_PARAGRAPH_START = re.compile(rb"\\pard\\plain")
_PARAGRAPH_END = re.compile(rb"\\par(?![A-Za-z])")
_CENTERED_TITLE_STYLE = re.compile(rb"\\s1(?![0-9])[^\r\n]{0,160}\\qc")
_GARANT_HEADING_URL = re.compile(
    rb'HYPERLINK "(https://internet\.garant\.ru/document/redirect/[0-9]{1,20}/0)"'
)
_NUMBER = re.compile(r"(?:\bN|№)\s*([0-9]+(?:[-‐‑–][А-Яа-яA-Za-z0-9]+)?)")
_DATE = re.compile(
    r"\b([0-9]{1,2})\s+"
    r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"\s+([0-9]{4})\s*(?:г\.|года)?",
    re.IGNORECASE,
)
_PART_HEADING = re.compile(r"(?m)^Часть (первая|вторая|третья|четвертая)[ \t]*$")
_SIGNATURE = re.compile(
    r"(?m)([0-9]{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|"
    r"сентября|октября|ноября|декабря)\s+[0-9]{4}\s*(?:г\.|года)?)"
    r"[ \t]*\n[ \t]*(?:N|№)[ \t]*([0-9]+(?:[-‐‑–][А-Яа-яA-Za-z0-9]+)?)",
    re.IGNORECASE,
)
_MONTHS = {
    name: index for index, name in enumerate((
        "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
        "сентября", "октября", "ноября", "декабря",
    ), start=1)
}
_PART_NAMES = ("первая", "вторая", "третья", "четвертая")
_IDENTITY_BLOCKERS = (
    "CANONICAL_IDENTITY_UNVERIFIED", "PUBLICATION_DATE_UNVERIFIED",
    "VERSION_DATE_UNVERIFIED", "EFFECTIVE_DATE_UNVERIFIED",
    "TEXT_COMPLETENESS_UNVERIFIED",
)


@dataclass(frozen=True)
class NormativePartCandidate:
    part_key: str
    title: str
    document_type: str | None
    issuer: str | None
    official_number: str | None
    adoption_date_candidate: date | None
    identity_sha256: str | None
    source_url: str | None
    identity_locator: str
    text_start: int | None
    text_end: int | None
    text_sha256: str | None
    blockers: tuple[str, ...]
    publication_date: None = None
    version_date: None = None
    effective_from: None = None


@dataclass(frozen=True)
class NormativePreparationCandidate:
    title: str
    raw_sha256: str
    normalized_sha256: str
    source_url: str | None
    source_locator: str | None
    parts: tuple[NormativePartCandidate, ...]
    blockers: tuple[str, ...]


def _clean(value: str) -> str:
    return " ".join(value.replace("\ufeff", "").replace("\u00a0", " ").split())


def _heading_source(raw: bytes, title: str) -> tuple[str | None, str | None]:
    """Accept only the link whose displayed text is the extracted first heading."""
    heading_region = raw[:_TITLE_SCAN_BYTES]
    for start in _PARAGRAPH_START.finditer(heading_region):
        end = _PARAGRAPH_END.search(heading_region, start.end())
        if end is None:
            break
        paragraph = heading_region[start.start():end.end()]
        if _CENTERED_TITLE_STYLE.search(paragraph[:200]) is None:
            continue
        links = list(_GARANT_HEADING_URL.finditer(paragraph))
        if len(links) != 1 or paragraph.count(b"HYPERLINK") != 1:
            return None, None
        result_start = paragraph.find(b"\\fldrslt", links[0].end())
        if result_start < 0:
            return None, None
        displayed_raw = paragraph[result_start:]
        if b"\\'" in displayed_raw or b"\\u" in displayed_raw:
            return None, None  # Unsupported heading encoding: ask for manual comparison.
        try:
            displayed = displayed_raw.decode("cp1251")
        except UnicodeDecodeError:
            return None, None
        displayed = re.sub(r"\\[A-Za-z]+-?\d* ?", "", displayed)
        displayed = displayed.replace("{", "").replace("}", "")
        if _clean(displayed) != _clean(title):
            return None, None
        url = links[0].group(1).decode("ascii")
        return url, f"RTF first centered title paragraph bytes {start.start()}:{end.end()}"
    return None, None


def _date_candidate(value: str) -> date | None:
    match = _DATE.search(_clean(value))
    if match is None:
        return None
    try:
        return date(int(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1)))
    except ValueError:
        return None


def _document_type(title: str) -> str | None:
    for prefix, kind in (
        ("Федеральный закон", "Федеральный закон"),
        ("Закон РФ", "Закон РФ"),
        ("Основы законодательства", "Основы законодательства"),
        ("Приказ", "Приказ"),
        ("Постановление", "Постановление"),
        ("Обзор", "Обзор судебной практики"),
    ):
        if title.startswith(prefix):
            return kind
    if "кодекс российской федерации" in title.casefold():
        return "Кодекс"
    return None


def _issuer(title: str) -> str | None:
    if title.startswith(("Приказ ", "Постановление ")):
        match = re.match(r"^(?:Приказ|Постановление) (.+?) от [0-9]{1,2} ", title)
        if match is not None:
            return match.group(1)
    if "утв. Президиумом Верховного Суда РФ" in title:
        return "Президиум Верховного Суда РФ"
    return None


def _identity_hash(
    kind: str | None, issuer: str | None, number: str | None, act_date: date | None,
) -> str | None:
    if kind is None or number is None or act_date is None:
        return None
    # A candidate comparison token, never the corpus canonical key.
    identity = "|".join((kind, issuer or "", act_date.isoformat(), number.upper()))
    return hashlib.sha256(identity.encode()).hexdigest()


def _part_candidate(
    *, key: str, title: str, identity_text: str, identity_locator: str,
    source_url: str | None, whole_text: str, start: int | None, end: int | None,
    number: str | None = None, act_date: date | None = None,
) -> NormativePartCandidate:
    kind = _document_type(title)
    issuer = _issuer(title)
    if number is None:
        found = _NUMBER.search(_clean(identity_text))
        number = found.group(1).replace("‐", "-").replace("‑", "-").replace("–", "-") \
            if found else None
    if act_date is None:
        act_date = _date_candidate(identity_text)
    scoped_hash = hashlib.sha256(whole_text[start:end].encode()).hexdigest() \
        if start is not None and end is not None else None
    blockers = list(_IDENTITY_BLOCKERS)
    for missing, value in (
        ("DOCUMENT_TYPE_NOT_FOUND", kind), ("ISSUER_NOT_IN_HEADING", issuer),
        ("OFFICIAL_NUMBER_NOT_FOUND", number), ("ACT_DATE_NOT_FOUND", act_date),
    ):
        if value is None:
            blockers.append(missing)
    if scoped_hash is None:
        blockers.append("CODE_PART_BOUNDARIES_UNVERIFIED")
    if source_url is None:
        blockers.append("SOURCE_HEADING_UNVERIFIED")
    return NormativePartCandidate(
        part_key=key, title=title, document_type=kind, issuer=issuer,
        official_number=number, adoption_date_candidate=act_date,
        identity_sha256=_identity_hash(kind, issuer, number, act_date),
        source_url=source_url, identity_locator=identity_locator,
        text_start=start, text_end=end, text_sha256=scoped_hash,
        blockers=tuple(blockers),
    )


def _bundle_parts(
    title: str, text: str, source_url: str | None, expected: tuple[str, ...],
) -> tuple[tuple[NormativePartCandidate, ...], bool]:
    markers = list(_PART_HEADING.finditer(text))
    valid = len(markers) == len(expected) and tuple(m.group(1) for m in markers) == expected
    if valid:
        first_lines = text[:markers[0].start()].splitlines()
        valid = len([line for line in first_lines if line.strip()]) == 1
    if valid:
        for marker in markers:
            following = text[marker.end():].splitlines()[:3]
            if not any(re.match(r"Принят[ао]? Государственной Думой", x.strip())
                       for x in following):
                valid = False
                break
    parts = []
    for index, name in enumerate(expected):
        start = (0 if index == 0 else markers[index].start()) if valid else None
        end = (markers[index + 1].start() if index + 1 < len(markers) else len(text)) \
            if valid else None
        signature = None
        if start is not None and end is not None:
            signature_tail = _clean_signature_text(text[start:end])[-500:]
            signature_matches = list(_SIGNATURE.finditer(signature_tail))
            signature = signature_matches[-1] if signature_matches else None
        act_date = _date_candidate(signature.group(1)) if signature else None
        number = signature.group(2) if signature else None
        parts.append(_part_candidate(
            key=f"part-{index + 1}", title=f"{title} — часть {name}",
            identity_text="", identity_locator=(
                f"normalized part {index + 1} signature tail; unverified conversion"
            ), source_url=source_url, whole_text=text, start=start, end=end,
            number=number, act_date=act_date,
        ))
    return tuple(parts), valid


def _clean_signature_text(text: str) -> str:
    return text.replace("\u00a0", " ")


def inspect_normative_rtf(raw: bytes, normalized_text: str) -> NormativePreparationCandidate:
    """Build candidates only; every date and text scope still needs human verification."""
    if not raw or len(raw) > _MAX_RAW_BYTES or not is_safe_rtf(raw):
        raise ValueError("unsafe or oversized normative RTF")
    if not normalized_text.strip() or len(normalized_text) > _MAX_TEXT_CHARS:
        raise ValueError("empty or oversized normalized text")
    if "\x00" in normalized_text or "\ufffd" in normalized_text:
        raise ValueError("invalid normalized text")
    title = next(line.strip().lstrip("\ufeff") for line in normalized_text.splitlines()
                 if line.strip().lstrip("\ufeff"))
    if len(title) > 1000:
        raise ValueError("normalized title exceeds limit")
    source_url, source_locator = _heading_source(raw, title)
    blockers = list(_IDENTITY_BLOCKERS)
    if source_url is None:
        blockers.append("SOURCE_HEADING_UNVERIFIED")
    if title.startswith("Гражданский кодекс Российской Федерации"):
        parts, boundaries_ok = _bundle_parts(title, normalized_text, source_url, _PART_NAMES)
    elif title.startswith("Налоговый кодекс Российской Федерации"):
        parts, boundaries_ok = _bundle_parts(title, normalized_text, source_url,
                                             _PART_NAMES[:2])
    else:
        parts = (_part_candidate(
            key="part-1", title=title, identity_text=title,
            identity_locator="normalized first title line; unverified conversion",
            source_url=source_url, whole_text=normalized_text,
            start=0, end=len(normalized_text),
        ),)
        boundaries_ok = True
    if not boundaries_ok:
        blockers.append("CODE_PART_BOUNDARIES_UNVERIFIED")
    return NormativePreparationCandidate(
        title=title, raw_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_sha256=hashlib.sha256(normalized_text.encode()).hexdigest(),
        source_url=source_url, source_locator=source_locator, parts=parts,
        blockers=tuple(blockers),
    )
