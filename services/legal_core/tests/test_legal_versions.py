from datetime import date
from uuid import UUID

from legal_core.legal_corpus import LegalVersionCandidate, select_applicable_version


def version(
    identifier: int,
    *,
    effective_from: date,
    effective_to: date | None,
    approval_state: str = "APPROVED",
) -> LegalVersionCandidate:
    return LegalVersionCandidate(
        id=UUID(f"00000000-0000-0000-0000-{identifier:012d}"),
        effective_from=effective_from,
        effective_to=effective_to,
        approval_state=approval_state,
    )


def test_half_open_effective_date_boundary_selects_correct_decree() -> None:
    decree_736 = version(
        736,
        effective_from=date(2023, 9, 1),
        effective_to=date(2026, 9, 1),
    )
    decree_659 = version(
        659,
        effective_from=date(2026, 9, 1),
        effective_to=date(2031, 9, 1),
    )

    assert select_applicable_version([decree_736, decree_659], date(2026, 8, 31)) == decree_736
    assert select_applicable_version([decree_736, decree_659], date(2026, 9, 1)) == decree_659


def test_unapproved_version_is_never_selected() -> None:
    pending = version(
        659,
        effective_from=date(2026, 9, 1),
        effective_to=None,
        approval_state="REVIEW_REQUIRED",
    )

    assert select_applicable_version([pending], date(2026, 9, 1)) is None

