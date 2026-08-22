from datetime import date
from pathlib import Path
from uuid import UUID

from legal_core.corpus_loader import load_manifest
from legal_core.legal_corpus import LegalVersionCandidate, select_applicable_version

ROOT = Path(__file__).parents[3]
CORPUS = ROOT / "services/legal_core/corpus"


def test_pp736_pp659_manifest_boundary_is_contiguous_and_half_open() -> None:
    pp736 = load_manifest(CORPUS / "initial_pp736.json")
    pp659 = load_manifest(CORPUS / "initial_pp659.json")

    assert pp736.effective_to == pp659.effective_from == date(2026, 9, 1)
    candidates = [
        LegalVersionCandidate(
            id=UUID("00000000-0000-0000-0000-000000000736"),
            effective_from=pp736.effective_from,
            effective_to=pp736.effective_to,
            approval_state="APPROVED",
        ),
        LegalVersionCandidate(
            id=UUID("00000000-0000-0000-0000-000000000659"),
            effective_from=pp659.effective_from,
            effective_to=pp659.effective_to,
            approval_state="APPROVED",
        ),
    ]

    assert select_applicable_version(candidates, date(2026, 8, 31)) == candidates[0]
    assert select_applicable_version(candidates, date(2026, 9, 1)) == candidates[1]
    assert select_applicable_version(candidates, date(2031, 9, 1)) is None


def test_both_seed_manifests_remain_non_production_excerpts() -> None:
    for name in ("initial_pp736.json", "initial_pp659.json"):
        manifest = load_manifest(CORPUS / name)
        assert manifest.approval_state == "REVIEW_REQUIRED"
        assert manifest.artifact_kind == "NORMALIZED_EXCERPT"
