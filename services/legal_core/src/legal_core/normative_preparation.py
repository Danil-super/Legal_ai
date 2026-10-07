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
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    is_safe_rtf,
)
from legal_core.material_preparation import MaterialPreparationInput
from legal_core.models import (
    LegalDocument,
    LegalFragment,
    LegalMaterialPreparation,
    LegalPreparedPartVersion,
    LegalReviewMaterial,
    LegalSource,
    LegalVersion,
    User,
)

_MAX_RAW_BYTES = 50_000_000
_MAX_TEXT_CHARS = 25_000_000
_TITLE_SCAN_BYTES = 64_000
_PARAGRAPH_START = re.compile(rb"\\pard\\plain")
_PARAGRAPH_END = re.compile(rb"\\par(?![A-Za-z])")
_CENTERED_TITLE_STYLE = re.compile(rb"\\s1(?![0-9])[^\r\n]{0,160}\\qc")
_GARANT_HEADING_URL = re.compile(
    rb'HYPERLINK "(https://internet\.garant\.ru/document/redirect/[0-9]{1,20}/0)"'
)
_ACT_NUMBER = r"[0-9]+(?:[-‐‑–][А-Яа-яA-Za-z0-9]+|[А-Яа-яA-Za-z]+)?"
_NUMBER = re.compile(r"(?:\bN|№)\s*(" + _ACT_NUMBER + r")(?![А-Яа-яA-Za-z0-9])")
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
    r"[ \t]*\n[ \t]*(?:N|№)[ \t]*(" + _ACT_NUMBER + r")(?![А-Яа-яA-Za-z0-9])",
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
    title = None
    for line in normalized_text.splitlines():
        candidate = line.replace("\ufeff", "").strip()
        if candidate:
            title = candidate
            break
    if title is None:
        raise ValueError("empty normalized title")
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


async def bind_prepared_part(
    session: AsyncSession, *, preparation_id: UUID, part_key: str,
    legal_version_id: UUID, actor_user_id: UUID,
) -> LegalPreparedPartVersion:
    """Bind one exact RTF part in the caller's transaction, without approving it.

    The corpus version must already have been ingested through ``corpus_loader``.
    An association failure rolls back this transaction, but does not erase an
    earlier, unbound REVIEW_REQUIRED import. That import is not legal evidence.
    """
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", part_key) is None:
        raise ValueError("invalid prepared part key")
    actor = await session.get(User, actor_user_id)
    if actor is None or actor.status != "ACTIVE" or actor.system_role != "LEGAL_EDITOR":
        raise PermissionError("active LEGAL_EDITOR role is required")
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(
        f"prepared-part:{preparation_id}:{part_key}", 730659,
    ))))
    prepared = await session.scalar(select(LegalMaterialPreparation).where(
        LegalMaterialPreparation.id == preparation_id,
    ))
    if prepared is None:
        raise ValueError("prepared material is missing")
    existing = await session.scalar(select(LegalPreparedPartVersion).where(
        LegalPreparedPartVersion.preparation_id == preparation_id,
        LegalPreparedPartVersion.part_key == part_key,
    ))
    if existing is not None:
        if existing.legal_version_id != legal_version_id:
            raise ValueError("prepared part is already bound to another version")
        return existing
    latest_revision = await session.scalar(
        select(func.max(LegalMaterialPreparation.revision)).where(
            LegalMaterialPreparation.material_id == prepared.material_id,
        )
    )
    if prepared.revision != latest_revision:
        raise ValueError("prepared material has a newer revision")
    original = await session.get(LegalReviewMaterial, prepared.material_id)
    version = await session.get(LegalVersion, legal_version_id)
    if original is None or version is None or (
        original.kind != "LEGAL_COPY" or prepared.kind != "NORMATIVE"
        or original.raw_mime_type != "application/rtf"
        or version.raw_mime_type != "application/rtf"
        or version.artifact_kind != "THIRD_PARTY_VERIFIED_COPY"
        or version.normalization_scope != "FULL_DOCUMENT"
        or version.approval_state != "REVIEW_REQUIRED"
        or version.artifact_retrieved_at is None
        or version.parser_version != prepared.metadata_json.get("parser_version")
        or prepared.raw_sha256 != original.raw_sha256
        or version.raw_sha256 != original.raw_sha256
        or version.raw_bytes != original.raw_bytes
    ):
        raise ValueError("version does not match the exact normative original")
    payload = MaterialPreparationInput.model_validate(
        prepared.metadata_json | {"normalized_text": prepared.normalized_text}
    )
    if payload.digest() != prepared.preparation_sha256 or (
        payload.extraction_scope != "FULL_DOCUMENT" or not payload.parts
        or payload.source_url is None
    ):
        raise ValueError("normative preparation is not complete")
    candidate = inspect_normative_rtf(original.raw_bytes, prepared.normalized_text)
    if candidate.raw_sha256 != payload.raw_sha256 or (
        candidate.title != payload.title
        or candidate.normalized_sha256 != payload.normalized_sha256
        or candidate.source_url != payload.source_url
        or candidate.source_locator != payload.source_locator
        or [part.part_key for part in candidate.parts] != [part.part_key for part in payload.parts]
    ):
        raise ValueError("prepared source or document-part boundaries are unverified")
    selected = None
    for part, parsed in zip(payload.parts, candidate.parts, strict=True):
        if part.text_start != parsed.text_start or part.text_end != parsed.text_end or (
            part.text_sha256 is None or part.text_sha256 != parsed.text_sha256
            or (parsed.document_type is not None and part.document_type != parsed.document_type)
            or (parsed.official_number is not None
                and part.official_number != parsed.official_number)
            or (parsed.adoption_date_candidate is not None
                and part.adoption_date != parsed.adoption_date_candidate)
        ):
            raise ValueError("prepared part differs from the exact extracted original")
        if part.part_key == part_key:
            selected = part
    if selected is None or selected.text_start is None or (
        selected.text_end is None or selected.text_sha256 is None
    ):
        raise ValueError("requested part is not completely prepared")
    required = (
        "title", "canonical_key", "document_type", "issuer", "official_number",
        "adoption_date", "publication_date", "version_date", "effective_from",
    )
    if any(getattr(selected, field) is None or not selected.evidence.get(field)
           for field in required):
        raise ValueError("canonical identity or edition lacks evidence")
    document = await session.get(LegalDocument, version.document_id)
    source = await session.get(LegalSource, version.source_id)
    if document is None or source is None or source.source_key != "garant" or (
        source.trust_level != "VERIFIED_COPY" or source.status not in {"DRAFT", "APPROVED"}
        or source.allowed_hosts != ["internet.garant.ru"]
        or version.source_url != payload.source_url
        or version.source_external_id != payload.source_url.rsplit("/", 2)[-2]
        or (
            document.canonical_key, document.document_type, document.title,
            document.issuer, document.official_number, document.adoption_date,
        ) != (
            selected.canonical_key, selected.document_type, selected.title,
            selected.issuer, selected.official_number, selected.adoption_date,
        )
        or (
            version.publication_date, version.version_date,
            version.effective_from, version.effective_to,
        ) != (
            selected.publication_date, selected.version_date,
            selected.effective_from, selected.effective_to,
        )
    ):
        raise ValueError("corpus identity, source or edition differs from preparation")
    scoped_text = prepared.normalized_text[selected.text_start:selected.text_end]
    if version.normalized_text != scoped_text or (
        version.normalized_sha256 != selected.text_sha256
        or hashlib.sha256(scoped_text.encode()).hexdigest() != selected.text_sha256
    ):
        raise ValueError("corpus text does not match the scoped original")
    fragments = list((await session.scalars(select(LegalFragment).where(
        LegalFragment.version_id == version.id,
    ).order_by(LegalFragment.ordinal))).all())
    fragment_models = [CorpusFragment(
        ordinal=item.ordinal, article=item.article, part=item.part, point=item.point,
        heading=item.heading, structural_path=item.structural_path,
        text=item.fragment_text,
    ) for item in fragments]
    if not fragment_models or corpus_fragments_sha256(fragment_models) != (
        version.fragments_sha256
    ) or any(item.text not in scoped_text for item in fragment_models):
        raise ValueError("corpus fragments do not match the scoped text")
    binding = LegalPreparedPartVersion(
        material_id=original.id, preparation_id=prepared.id,
        raw_sha256=original.raw_sha256, part_key=part_key,
        part_text_sha256=selected.text_sha256,
        legal_version_id=version.id, created_by_user_id=actor.id,
    )
    session.add(binding)
    await session.flush()
    return binding
