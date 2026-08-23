"""Deterministic structural comparison of two selected legal-fragment revisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from legal_core.corpus_loader import CorpusFragment


class ChangeKind(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    CHANGED = "CHANGED"


@dataclass(frozen=True, slots=True)
class StructuralChange:
    kind: ChangeKind
    structural_path: str
    previous_text_sha256: str | None
    candidate_text_sha256: str | None


@dataclass(frozen=True, slots=True)
class StructuralDiff:
    changes: tuple[StructuralChange, ...]
    sha256: str


def _by_path(fragments: Sequence[CorpusFragment]) -> dict[str, CorpusFragment]:
    by_path = {fragment.structural_path: fragment for fragment in fragments}
    if len(by_path) != len(fragments):
        raise ValueError("fragment structural paths must be unique")
    return by_path


def _text_sha256(fragment: CorpusFragment) -> str:
    return hashlib.sha256(fragment.text.encode()).hexdigest()


def _digest(changes: Sequence[StructuralChange]) -> str:
    payload = [
        {
            "candidateTextSha256": change.candidate_text_sha256,
            "kind": change.kind.value,
            "previousTextSha256": change.previous_text_sha256,
            "structuralPath": change.structural_path,
        }
        for change in changes
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def diff_fragment_selections(
    *,
    previous: Sequence[CorpusFragment],
    candidate: Sequence[CorpusFragment],
) -> StructuralDiff:
    """Return an ordered, content-hash-only diff suitable for a legal-editor review queue."""

    previous_by_path = _by_path(previous)
    candidate_by_path = _by_path(candidate)
    changes: list[StructuralChange] = []

    for structural_path in sorted(previous_by_path.keys() | candidate_by_path.keys()):
        old = previous_by_path.get(structural_path)
        new = candidate_by_path.get(structural_path)
        if old is None and new is not None:
            changes.append(
                StructuralChange(
                    kind=ChangeKind.ADDED,
                    structural_path=structural_path,
                    previous_text_sha256=None,
                    candidate_text_sha256=_text_sha256(new),
                )
            )
        elif old is not None and new is None:
            changes.append(
                StructuralChange(
                    kind=ChangeKind.REMOVED,
                    structural_path=structural_path,
                    previous_text_sha256=_text_sha256(old),
                    candidate_text_sha256=None,
                )
            )
        elif old is not None and new is not None:
            old_sha256 = _text_sha256(old)
            new_sha256 = _text_sha256(new)
            if old_sha256 != new_sha256:
                changes.append(
                    StructuralChange(
                        kind=ChangeKind.CHANGED,
                        structural_path=structural_path,
                        previous_text_sha256=old_sha256,
                        candidate_text_sha256=new_sha256,
                    )
                )

    frozen_changes = tuple(changes)
    return StructuralDiff(changes=frozen_changes, sha256=_digest(frozen_changes))
