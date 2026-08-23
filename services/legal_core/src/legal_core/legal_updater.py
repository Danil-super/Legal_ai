"""Deterministic non-promoting update candidate construction for Legal Core."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from legal_core.corpus_loader import CorpusFragment, corpus_fragments_sha256
from legal_core.legal_diff import StructuralDiff, diff_fragment_selections

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class LegalUpdateCandidate:
    document_key: str
    status: str
    raw_sha256: str
    normalized_sha256: str
    fragments_sha256: str
    structural_diff: StructuralDiff
    candidate_sha256: str
    auto_promotion_allowed: bool = False


def _validated_sha256(value: str, *, name: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} SHA-256 must be a lowercase 64-character digest")
    return value


def build_review_candidate(
    *,
    document_key: str,
    previous: Sequence[CorpusFragment],
    proposed: Sequence[CorpusFragment],
    raw_sha256: str,
    normalized_sha256: str,
) -> LegalUpdateCandidate:
    """Create a review candidate; this function has no path to APPROVED state."""

    if not document_key or len(document_key) > 120:
        raise ValueError("document key must be between 1 and 120 characters")
    raw_sha256 = _validated_sha256(raw_sha256, name="raw")
    normalized_sha256 = _validated_sha256(normalized_sha256, name="normalized")
    fragments_sha256 = corpus_fragments_sha256(list(proposed))
    structural_diff = diff_fragment_selections(previous=previous, candidate=proposed)
    digest_input = {
        "documentKey": document_key,
        "fragmentsSha256": fragments_sha256,
        "normalizedSha256": normalized_sha256,
        "rawSha256": raw_sha256,
        "structuralDiffSha256": structural_diff.sha256,
    }
    candidate_sha256 = hashlib.sha256(
        json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return LegalUpdateCandidate(
        document_key=document_key,
        status="REVIEW_REQUIRED",
        raw_sha256=raw_sha256,
        normalized_sha256=normalized_sha256,
        fragments_sha256=fragments_sha256,
        structural_diff=structural_diff,
        candidate_sha256=candidate_sha256,
    )
