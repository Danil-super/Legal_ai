"""Local direct-identifier pseudonymisation for bounded external-agent context.

This is a safety boundary, not a claim of irreversible anonymisation.  Medical facts remain
sensitive even after direct identifiers are removed, so callers must still minimise the context
and follow the configured provider/compliance policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


_EMAIL: Final = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}(?![\w.-])")
_PHONE: Final = re.compile(
    r"(?<!\d)(?:\+7|7|8)[\s\-()]*(?:\d[\s\-()]*){10}(?!\d)"
)
_PASSPORT: Final = re.compile(r"(?<!\d)\d{4}[\s-]?\d{6}(?!\d)")
_SNILS: Final = re.compile(r"(?<!\d)\d{3}[ -]?\d{3}[ -]?\d{3}[ -]?\d{2}(?!\d)")
_LONG_IDENTIFIER: Final = re.compile(r"(?<!\d)\d{14,20}(?!\d)")
_RUSSIAN_NAME_WORD = r"[А-ЯЁ][а-яё]{1,30}(?:-[А-ЯЁ][а-яё]{1,30})?"
_INITIALS_NAME: Final = re.compile(
    rf"(?<![А-ЯЁа-яё])(?:"
    rf"{_RUSSIAN_NAME_WORD}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.|"
    rf"[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*{_RUSSIAN_NAME_WORD}"
    rf")(?![А-ЯЁа-яё])"
)
_IDENTIFIED_FULL_NAME: Final = re.compile(
    rf"(?<![А-ЯЁа-яё])(?:Пациент(?:а|у|ом|е)?|пациент(?:а|у|ом|е)?|ФИО|фио|"
    rf"Ф\.?\s*И\.?\s*О\.?|ф\.?\s*и\.?\s*о\.?|"
    rf"Фамилия\s+имя(?:\s+отчество)?|фамилия\s+имя(?:\s+отчество)?)\s*[:—-]?\s*"
    rf"{_RUSSIAN_NAME_WORD}(?:\s+{_RUSSIAN_NAME_WORD}){{1,2}}"
    rf"(?![А-ЯЁа-яё])"
)


@dataclass(frozen=True, slots=True)
class PseudonymizedText:
    text: str
    replacement_counts: dict[str, int]

    @property
    def changed(self) -> bool:
        return any(self.replacement_counts.values())


def _replace(pattern: re.Pattern[str], text: str, placeholder: str) -> tuple[str, int]:
    return pattern.subn(placeholder, text)


def pseudonymize_text(
    text: str,
    *,
    known_identifiers: dict[str, str] | None = None,
) -> PseudonymizedText:
    """Replace direct identifiers locally before a text may enter an external LLM boundary.

    ``known_identifiers`` contains values already known to the trusted backend (for example a
    patient or doctor name parsed from a clinic document) mapped to stable placeholders such as
    ``[PATIENT_1]``.  Values shorter than three characters are ignored to avoid destructive
    substring replacement.
    """

    result = text
    counts: dict[str, int] = {}

    for label, pattern, placeholder in (
        ("email", _EMAIL, "[EMAIL]"),
        ("phone", _PHONE, "[PHONE]"),
        ("passport", _PASSPORT, "[PASSPORT]"),
        ("snils", _SNILS, "[SNILS]"),
        ("long_identifier", _LONG_IDENTIFIER, "[IDENTIFIER]"),
        ("initials_name", _INITIALS_NAME, "[PERSON_NAME]"),
    ):
        result, count = _replace(pattern, result, placeholder)
        counts[label] = count

    known_count = 0
    for raw_value, placeholder in sorted(
        (known_identifiers or {}).items(), key=lambda item: len(item[0]), reverse=True
    ):
        normalized = raw_value.strip()
        if len(normalized) < 3:
            continue
        result, count = re.subn(re.escape(normalized), placeholder, result, flags=re.IGNORECASE)
        known_count += count
    counts["known_identifier"] = known_count
    result, counts["identified_full_name"] = _replace(
        _IDENTIFIED_FULL_NAME,
        result,
        "[PERSON_NAME]",
    )

    return PseudonymizedText(text=result, replacement_counts=counts)


def contains_obvious_direct_identifier(text: str) -> bool:
    """Conservative post-redaction guard used immediately before an external provider call."""

    return any(
        pattern.search(text) is not None
        for pattern in (
            _EMAIL,
            _PHONE,
            _PASSPORT,
            _SNILS,
            _INITIALS_NAME,
            _IDENTIFIED_FULL_NAME,
        )
    )
