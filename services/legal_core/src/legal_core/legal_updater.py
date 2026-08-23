"""Deterministic non-promoting update candidate construction for Legal Core."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.corpus_loader import CorpusFragment, corpus_fragments_sha256
from legal_core.legal_diff import StructuralDiff, diff_fragment_selections
from legal_core.models import LegalSource, LegalUpdateReviewItem, LegalVersion

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


@dataclass(frozen=True, slots=True)
class ReviewQueuePayload:
    """Hash-only immutable content of one legal-editor review item."""

    raw_sha256: str
    normalized_sha256: str
    fragments_sha256: str
    structural_diff_sha256: str
    structural_diff_json: list[dict[str, str | None]]
    candidate_sha256: str
    status: str = "REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class ReviewQueueReceipt:
    review_item_id: UUID
    created: bool


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


def review_queue_payload(candidate: LegalUpdateCandidate) -> ReviewQueuePayload:
    """Serialize a candidate without retaining text from a proposed legal revision."""

    if candidate.status != "REVIEW_REQUIRED" or candidate.auto_promotion_allowed:
        raise ValueError("only non-promoting review candidates may enter the queue")
    return ReviewQueuePayload(
        raw_sha256=candidate.raw_sha256,
        normalized_sha256=candidate.normalized_sha256,
        fragments_sha256=candidate.fragments_sha256,
        structural_diff_sha256=candidate.structural_diff.sha256,
        structural_diff_json=[
            {
                "candidateTextSha256": change.candidate_text_sha256,
                "kind": change.kind.value,
                "previousTextSha256": change.previous_text_sha256,
                "structuralPath": change.structural_path,
            }
            for change in candidate.structural_diff.changes
        ],
        candidate_sha256=candidate.candidate_sha256,
    )


async def queue_review_candidate(
    session: AsyncSession,
    *,
    source_id: UUID,
    document_id: UUID,
    previous_legal_version_id: UUID | None,
    candidate_legal_version_id: UUID,
    candidate: LegalUpdateCandidate,
) -> ReviewQueueReceipt:
    """Persist a review-only updater result after deterministic identity checks.

    This service accepts pre-fetched, locally parsed artifacts only. It neither fetches a URL
    nor changes a legal version/source lifecycle state.
    """

    payload = review_queue_payload(candidate)
    existing = await session.scalar(
        select(LegalUpdateReviewItem).where(
            LegalUpdateReviewItem.candidate_sha256 == payload.candidate_sha256
        )
    )
    expected_identity = (
        source_id,
        document_id,
        previous_legal_version_id,
        candidate_legal_version_id,
        payload.raw_sha256,
        payload.normalized_sha256,
        payload.fragments_sha256,
        payload.structural_diff_sha256,
        payload.structural_diff_json,
    )
    if existing is not None:
        stored_identity = (
            existing.source_id,
            existing.document_id,
            existing.previous_legal_version_id,
            existing.candidate_legal_version_id,
            existing.raw_sha256,
            existing.normalized_sha256,
            existing.fragments_sha256,
            existing.structural_diff_sha256,
            existing.structural_diff_json,
        )
        if stored_identity != expected_identity:
            raise ValueError("candidate checksum conflicts with an existing review item")
        return ReviewQueueReceipt(review_item_id=existing.id, created=False)

    source = await session.get(LegalSource, source_id)
    candidate_version = await session.get(LegalVersion, candidate_legal_version_id)
    if source is None or source.status != "APPROVED":
        raise PermissionError("an approved legal source is required for updater review")
    if candidate_version is None:
        raise LookupError("candidate legal version not found")
    if (
        candidate_version.source_id != source_id
        or candidate_version.document_id != document_id
        or candidate_version.approval_state != "REVIEW_REQUIRED"
    ):
        raise ValueError("candidate legal version is not a review-required version of this source")
    if (
        candidate_version.raw_sha256,
        candidate_version.normalized_sha256,
        candidate_version.fragments_sha256,
    ) != (
        payload.raw_sha256,
        payload.normalized_sha256,
        payload.fragments_sha256,
    ):
        raise ValueError("candidate checksums do not match the persisted legal version")
    if previous_legal_version_id is not None:
        previous = await session.get(LegalVersion, previous_legal_version_id)
        if previous is None or previous.document_id != document_id:
            raise ValueError("previous legal version must belong to the same document")

    item = LegalUpdateReviewItem(
        source_id=source_id,
        document_id=document_id,
        previous_legal_version_id=previous_legal_version_id,
        candidate_legal_version_id=candidate_legal_version_id,
        raw_sha256=payload.raw_sha256,
        normalized_sha256=payload.normalized_sha256,
        fragments_sha256=payload.fragments_sha256,
        structural_diff_sha256=payload.structural_diff_sha256,
        structural_diff_json=payload.structural_diff_json,
        candidate_sha256=payload.candidate_sha256,
        status=payload.status,
    )
    session.add(item)
    await session.flush()
    return ReviewQueueReceipt(review_item_id=item.id, created=True)
