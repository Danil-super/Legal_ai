"""Deterministic warning-only screening for risky clinic-document wording.

The scanner does not decide that a clause is unlawful. It only marks absolute internal wording that
must be checked against approved mandatory-law evidence before it influences an operational draft.
No external model or network call is involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClinicDocumentConflictHint:
    reason_code: str
    review_required: bool = True


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ABSOLUTE_NO_REFUND",
        re.compile(
            r"(?:возврат\w*(?:\s+денежн\w*\s+средств\w*)?\s+"
            r"(?:не\s+осуществ\w*|не\s+производ\w*|невозмож\w*))",
            re.IGNORECASE,
        ),
    ),
    (
        "ABSOLUTE_NO_LIABILITY",
        re.compile(
            r"(?:клиник\w*|исполнител\w*)\s+не\s+нес[её]т\s+"
            r"(?:никак\w*\s+)?ответственност\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "WAIVER_OF_ALL_CLAIMS",
        re.compile(
            r"пациент\w*\s+(?:полностью\s+)?отказыва\w*\s+от\s+"
            r"(?:любых|всех)\s+претензи\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "INTERNAL_RULE_OVERRIDES_MANDATORY_RIGHTS",
        re.compile(
            r"(?:услови\w*\s+гаранти\w*|настоящ\w*\s+правил\w*)\s+"
            r"(?:име\w*\s+приоритет|исключа\w*)\s+.*(?:закон|прав\w*\s+пациент\w*)",
            re.IGNORECASE,
        ),
    ),
    (
        "AUTOMATIC_FAULT_SHIFT_TO_PATIENT",
        re.compile(
            r"нарушени\w*\s+рекомендаци\w*\s+автоматическ\w*\s+"
            r"(?:освобожда\w*|исключа\w*)",
            re.IGNORECASE,
        ),
    ),
)


def detect_potential_clinic_document_conflicts(
    text: str,
) -> tuple[ClinicDocumentConflictHint, ...]:
    """Return stable reason codes for narrowly defined absolute-risk wording.

    Normal explanatory language such as "refund does not automatically mean admission of fault"
    should not match these rules. A positive result means human/legal comparison is required, not
    that the clause is definitively invalid.
    """

    if not isinstance(text, str):
        raise TypeError("clinic document conflict scanner requires text")
    if not text.strip():
        return ()
    found: list[ClinicDocumentConflictHint] = []
    for reason_code, pattern in _RULES:
        if pattern.search(text) is not None:
            found.append(ClinicDocumentConflictHint(reason_code=reason_code))
    return tuple(found)
