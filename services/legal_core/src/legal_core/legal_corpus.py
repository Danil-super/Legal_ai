"""Pure effective-date rules shared by legal-corpus repositories and tests."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LegalVersionCandidate:
    id: UUID
    effective_from: date
    effective_to: date | None
    approval_state: str


def select_applicable_version(
    versions: list[LegalVersionCandidate], as_of_date: date
) -> LegalVersionCandidate | None:
    """Select one approved version using a half-open applicability interval."""

    applicable = [
        version
        for version in versions
        if version.approval_state == "APPROVED"
        and version.effective_from <= as_of_date
        and (version.effective_to is None or as_of_date < version.effective_to)
    ]
    if not applicable:
        return None
    if len(applicable) > 1:
        raise ValueError("overlapping approved legal versions")
    return applicable[0]

