"""Separate human confirmation ledger for clinical/reference materials.

Reference review proves only that the editor reviewed the immutable original as a
reference. It never creates a LegalVersion, legal approval, or retrievable legal
fragment.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.api_contracts import (
    LegalReferenceReviewCandidate,
    LegalReferenceReviewPreview,
    LegalReferenceReviewRequest,
    LegalReferenceReviewResponse,
)
from legal_core.case_api import ApiError
from legal_core.editor_groups import current_preparations
from legal_core.models import LegalMaterialPreparation, LegalReferenceReviewEvent, User
from legal_core.review_material_groups import ReviewGroup

_REFERENCE_KINDS = ("CLINICAL_REFERENCE", "REFERENCE_FORM")
_REFERENCE_GROUPS = frozenset(
    {"clinical", "labour", "courts", "privacy", "licensing", "healthcare", "general"}
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _reference_items() -> Any:
    preparations = current_preparations()
    return (
        select(
            preparations.c.id.label("preparation_id"),
            preparations.c.raw_sha256,
            preparations.c.title,
            preparations.c.kind,
            preparations.c.group_key,
            preparations.c.preparation_sha256,
            LegalReferenceReviewEvent.id.label("event_id"),
        )
        .outerjoin(
            LegalReferenceReviewEvent,
            LegalReferenceReviewEvent.preparation_id == preparations.c.id,
        )
        .where(preparations.c.kind.in_(_REFERENCE_KINDS))
        .subquery("current_reference_items")
    )


def _require_reference_group(group: str) -> None:
    if group not in _REFERENCE_GROUPS:
        raise ApiError(
            status_code=422, code="REFERENCE_REVIEW_GROUP_INVALID", message="Invalid group"
        )


async def reference_reviewable_count(session: AsyncSession, group: str) -> int:
    _require_reference_group(group)
    items = _reference_items()
    count = await session.scalar(
        select(func.count())
        .select_from(items)
        .where(items.c.group_key == group, items.c.event_id.is_(None))
    )
    return int(count or 0)


async def reference_review_preview(
    session: AsyncSession, group: str
) -> LegalReferenceReviewPreview:
    _require_reference_group(group)
    items = _reference_items()
    rows = (
        (
            await session.execute(
                select(items)
                .where(items.c.group_key == group)
                .order_by(items.c.title, items.c.preparation_id)
                .limit(201)
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > 200:
        raise ApiError(
            status_code=422,
            code="REFERENCE_REVIEW_GROUP_TOO_LARGE",
            message="Group too large",
        )
    identity: list[dict[str, object]] = []
    ready: list[LegalReferenceReviewCandidate] = []
    reviewed = 0
    for row in rows:
        is_reviewed = row["event_id"] is not None
        identity.append(
            {
                "preparationId": str(row["preparation_id"]),
                "rawSha256": row["raw_sha256"],
                "preparationSha256": row["preparation_sha256"],
                "group": row["group_key"],
                "reviewed": is_reviewed,
            }
        )
        if is_reviewed:
            reviewed += 1
        else:
            ready.append(
                LegalReferenceReviewCandidate(
                    preparationId=row["preparation_id"],
                    title=row["title"],
                    kind=row["kind"],
                )
            )
    return LegalReferenceReviewPreview(
        group=cast(ReviewGroup, group),
        snapshot=_digest(identity),
        ready=ready,
        alreadyReviewed=reviewed,
    )


async def confirm_reference_review(
    session: AsyncSession,
    *,
    group: str,
    payload: LegalReferenceReviewRequest,
    editor: User,
    idempotency_key: UUID,
) -> LegalReferenceReviewResponse:
    _require_reference_group(group)
    request_hash = _digest({"group": group, **payload.model_dump(mode="json", by_alias=True)})
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"reference-review:{editor.id}:{idempotency_key}", 736660)
            )
        )
    )
    root = await session.scalar(
        select(LegalReferenceReviewEvent)
        .where(
            LegalReferenceReviewEvent.actor_user_id == editor.id,
            LegalReferenceReviewEvent.idempotency_key == idempotency_key,
        )
    )
    requested_ids = sorted(payload.preparation_ids)
    if root is not None:
        if root.request_sha256 != request_hash or root.group_key != group:
            raise ApiError(status_code=422, code="IDEMPOTENCY_KEY_REUSED", message="Key reused")
        existing_ids = list(
            (
                await session.scalars(
                    select(LegalReferenceReviewEvent.preparation_id)
                    .where(LegalReferenceReviewEvent.batch_id == root.batch_id)
                    .order_by(LegalReferenceReviewEvent.preparation_id)
                )
            ).all()
        )
        if existing_ids != requested_ids:
            raise ApiError(
                status_code=409, code="REFERENCE_REVIEW_CHANGED", message="Refresh preview"
            )
        return LegalReferenceReviewResponse(
            batchId=root.batch_id,
            reviewedCount=len(existing_ids),
            preparationIds=existing_ids,
        )

    # Both ledgers are immutable, and the runtime role has no row-update privilege on them.
    # Transaction-scoped advisory locks provide the serialization that FOR UPDATE would otherwise
    # provide, without granting a mutable capability to the service role.
    for preparation_id in requested_ids:
        await session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"reference-preparation:{preparation_id}", 736661)
                )
            )
        )
    locked = list(
        (
            await session.scalars(
                select(LegalMaterialPreparation)
                .where(LegalMaterialPreparation.id.in_(requested_ids))
                .order_by(LegalMaterialPreparation.id)
            )
        ).all()
    )
    if len(locked) != len(requested_ids):
        raise ApiError(status_code=409, code="REFERENCE_REVIEW_CHANGED", message="Refresh preview")
    preview = await reference_review_preview(session, group)
    if preview.snapshot != payload.expected_snapshot or requested_ids != sorted(
        item.preparation_id for item in preview.ready
    ):
        raise ApiError(status_code=409, code="REFERENCE_REVIEW_CHANGED", message="Refresh preview")
    if any(item.kind not in _REFERENCE_KINDS or item.group_key != group for item in locked):
        raise ApiError(status_code=409, code="REFERENCE_REVIEW_CHANGED", message="Refresh preview")

    for index, preparation in enumerate(locked):
        session.add(
            LegalReferenceReviewEvent(
                preparation_id=preparation.id,
                actor_user_id=editor.id,
                raw_sha256=preparation.raw_sha256,
                group_key=group,
                batch_id=idempotency_key,
                idempotency_key=(
                    idempotency_key if index == 0 else uuid5(idempotency_key, str(preparation.id))
                ),
                request_sha256=request_hash,
                checks_json={"originalReviewed": True, "referenceOnlyUnderstood": True},
            )
        )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            status_code=409, code="REFERENCE_REVIEW_CHANGED", message="Refresh preview"
        ) from exc
    return LegalReferenceReviewResponse(
        batchId=idempotency_key,
        reviewedCount=len(locked),
        preparationIds=requested_ids,
    )
