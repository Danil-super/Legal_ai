"""Deterministic non-promoting update candidate construction for Legal Core."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.corpus_loader import CorpusFragment, corpus_fragments_sha256
from legal_core.legal_diff import StructuralDiff, diff_fragment_selections
from legal_core.models import (
    LegalDocument,
    LegalSource,
    LegalUpdateReviewItem,
    LegalUpdateRun,
    LegalVersion,
)

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


class UpdateRunStatus(StrEnum):
    NO_CHANGE = "NO_CHANGE"
    REVIEW_QUEUED = "REVIEW_QUEUED"
    FETCH_FAILED = "FETCH_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"


@dataclass(frozen=True, slots=True)
class UpdateRunPayload:
    idempotency_sha256: str
    result_sha256: str
    status: UpdateRunStatus
    failure_code: str | None
    review_item_id: UUID | None


@dataclass(frozen=True, slots=True)
class UpdateRunReceipt:
    update_run_id: UUID
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


def update_run_payload(
    *,
    status: UpdateRunStatus,
    idempotency_sha256: str,
    failure_code: str | None,
    review_item_id: UUID | None = None,
) -> UpdateRunPayload:
    """Create a redacted, deterministic record for one updater attempt."""

    idempotency_sha256 = _validated_sha256(idempotency_sha256, name="idempotency")
    failure_statuses = {
        UpdateRunStatus.FETCH_FAILED,
        UpdateRunStatus.PARSE_FAILED,
        UpdateRunStatus.VALIDATION_FAILED,
    }
    if status in failure_statuses:
        if review_item_id is not None:
            raise ValueError("failure status cannot reference a review item")
        if failure_code is None or re.fullmatch(r"[A-Z0-9_]{1,80}", failure_code) is None:
            raise ValueError("failure status requires a failure code")
    elif status is UpdateRunStatus.REVIEW_QUEUED:
        if review_item_id is None:
            raise ValueError("review-queued status requires a review item")
        if failure_code is not None:
            raise ValueError("review-queued status cannot have a failure code")
    elif status is UpdateRunStatus.NO_CHANGE:
        if review_item_id is not None or failure_code is not None:
            raise ValueError("no-change status cannot have a review item or failure code")
    else:  # pragma: no cover - StrEnum constrains ordinary callers.
        raise ValueError("unsupported update run status")

    result_sha256 = hashlib.sha256(
        "|".join(
            (
                idempotency_sha256,
                status.value,
                failure_code or "",
                str(review_item_id) if review_item_id is not None else "",
            )
        ).encode()
    ).hexdigest()
    return UpdateRunPayload(
        idempotency_sha256=idempotency_sha256,
        result_sha256=result_sha256,
        status=status,
        failure_code=failure_code,
        review_item_id=review_item_id,
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


async def record_update_run(
    session: AsyncSession,
    *,
    source_id: UUID,
    document_id: UUID | None,
    payload: UpdateRunPayload,
) -> UpdateRunReceipt:
    """Append a redacted updater outcome; retries with the same digest are idempotent."""

    existing = await session.scalar(
        select(LegalUpdateRun).where(
            LegalUpdateRun.idempotency_sha256 == payload.idempotency_sha256
        )
    )
    expected_identity = (
        source_id,
        document_id,
        payload.review_item_id,
        payload.result_sha256,
        payload.status.value,
        payload.failure_code,
    )
    if existing is not None:
        stored_identity = (
            existing.source_id,
            existing.document_id,
            existing.review_item_id,
            existing.result_sha256,
            existing.status,
            existing.failure_code,
        )
        if stored_identity != expected_identity:
            raise ValueError("idempotency checksum conflicts with an existing update run")
        return UpdateRunReceipt(update_run_id=existing.id, created=False)

    if await session.get(LegalSource, source_id) is None:
        raise LookupError("legal source not found")
    if document_id is not None and await session.get(LegalDocument, document_id) is None:
        raise LookupError("legal document not found")
    if payload.review_item_id is not None:
        review_item = await session.get(LegalUpdateReviewItem, payload.review_item_id)
        if (
            review_item is None
            or review_item.source_id != source_id
            or review_item.document_id != document_id
        ):
            raise ValueError("review item must belong to the same source and document")

    run = LegalUpdateRun(
        source_id=source_id,
        document_id=document_id,
        review_item_id=payload.review_item_id,
        idempotency_sha256=payload.idempotency_sha256,
        result_sha256=payload.result_sha256,
        status=payload.status.value,
        failure_code=payload.failure_code,
    )
    session.add(run)
    await session.flush()
    return UpdateRunReceipt(update_run_id=run.id, created=True)
