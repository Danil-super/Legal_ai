"""Explicit human batch approval using the existing immutable per-version ledger."""

import hashlib
import json
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.api_contracts import (
    LegalGroupApprovalRequest,
    LegalGroupApprovalResponse,
    LegalGroupBlocked,
    LegalGroupCandidate,
    LegalGroupPreview,
)
from legal_core.case_api import ApiError
from legal_core.editor_groups import editor_group_items
from legal_core.legal_approval import (
    ApprovalAttestation,
    LegalApprovalRejected,
    approve_legal_version_in_session,
    legal_approval_preflight_reason,
)
from legal_core.models import LegalApprovalEvent, LegalSource, LegalVersion, User
from legal_core.review_material_groups import ReviewGroup


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, default=str, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _attestation(version: LegalVersion, actor: int) -> ApprovalAttestation:
    # A read-only preflight uses these checks without recording a human decision.
    # The write path may call this only after validating all explicit declarations.
    return ApprovalAttestation(
        reviewer_telegram_user_id=actor,
        version_id=version.id,
        expected_sha256=version.raw_sha256,
        expected_normalized_sha256=version.normalized_sha256,
        expected_fragments_sha256=version.fragments_sha256,
        expected_effective_from=version.effective_from,
        expected_effective_to=version.effective_to,
        source_is_official=version.artifact_kind == "OFFICIAL_RAW",
        official_text_compared=True,
        artifact_is_complete=True,
        effective_dates_verified=True,
        fragments_verified=True,
    )


async def group_preview(session: AsyncSession, group: ReviewGroup) -> LegalGroupPreview:
    items = editor_group_items()
    rows = (
        (
            await session.execute(
                select(items)
                .where(items.c.group_key == group)
                .order_by(items.c.version_id, items.c.material_id)
                .limit(201)
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > 200:
        raise ApiError(status_code=422, code="LEGAL_GROUP_TOO_LARGE", message="Group exceeds limit")
    identity = []
    ready: list[LegalGroupCandidate] = []
    blocked = []
    approved = 0
    for row in rows:
        fingerprint = dict(row)
        reason: str | None = "METADATA_REQUIRED"
        if row["kind"] == "CLINICAL_REFERENCE":
            reason = "CLINICAL_REFERENCE_NOT_LEGAL_VERSION"
        elif row["version_id"] is not None:
            version = await session.get(LegalVersion, row["version_id"], populate_existing=True)
            if version is None:
                raise ApiError(status_code=409, code="LEGAL_GROUP_CHANGED", message="Group changed")
            attestation = _attestation(version, 1)
            fingerprint["version"] = attestation.model_dump(mode="json")
            if version.approval_state == "APPROVED":
                approved += 1
                identity.append(fingerprint)
                continue
            source = await session.get(LegalSource, version.source_id, populate_existing=True)
            if version.approval_state != "REVIEW_REQUIRED":
                reason = "VERSION_NOT_REVIEW_REQUIRED"
            elif version.raw_mime_type not in {"application/pdf", "application/rtf"} or not (
                0 < version.raw_size_bytes <= 50_000_000
            ):
                reason = "ARTIFACT_NOT_DELIVERABLE"
            elif source is None:
                reason = "SOURCE_MISSING"
            else:
                reason = await legal_approval_preflight_reason(
                    session, version, source, attestation
                )
            if reason is None:
                ready.append(
                    LegalGroupCandidate(
                        versionId=version.id,
                        title=row["title"],
                        effectiveFrom=version.effective_from,
                        effectiveTo=version.effective_to,
                    )
                )
        fingerprint["reason"] = reason
        identity.append(fingerprint)
        if reason is not None:
            blocked.append(LegalGroupBlocked(title=row["title"], reasonCode=reason))
    return LegalGroupPreview(
        group=group,
        snapshot=_digest(identity),
        ready=ready,
        blocked=blocked,
        alreadyApproved=approved,
    )


async def approve_group(
    session: AsyncSession,
    *,
    group: ReviewGroup,
    payload: LegalGroupApprovalRequest,
    editor: User,
    idempotency_key: UUID,
) -> LegalGroupApprovalResponse:
    request_hash = _digest({"group": group, **payload.model_dump(mode="json", by_alias=True)})
    ids = sorted(payload.version_ids)
    if len(ids) != len(set(ids)):
        raise ApiError(status_code=422, code="DUPLICATE_LEGAL_VERSION", message="Duplicate version")
    # Same namespace as single approval, and the same database unique constraint.
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(
                    f"legal-editor-approval:{editor.id}:{idempotency_key}", 736659
                )
            )
        )
    )
    replay = await session.scalar(
        select(LegalApprovalEvent).where(
            LegalApprovalEvent.actor_user_id == editor.id,
            LegalApprovalEvent.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if replay.request_sha256 != request_hash:
            raise ApiError(status_code=422, code="IDEMPOTENCY_KEY_REUSED", message="Key reused")
        return LegalGroupApprovalResponse(
            batchId=idempotency_key, approvedCount=len(ids), versionIds=ids
        )
    # Stable lock ordering prevents two overlapping group approvals from deadlocking.
    locked = (
        await session.execute(
            select(LegalVersion.id, LegalVersion.source_id)
            .where(LegalVersion.id.in_(ids))
            .order_by(LegalVersion.id)
            .with_for_update()
        )
    ).all()
    await session.execute(
        select(LegalSource.id)
        .where(LegalSource.id.in_({row.source_id for row in locked}))
        .order_by(LegalSource.id)
        .with_for_update()
    )
    preview = await group_preview(session, group)
    if preview.snapshot != payload.expected_snapshot or ids != sorted(
        item.version_id for item in preview.ready
    ):
        raise ApiError(status_code=409, code="LEGAL_GROUP_CHANGED", message="Refresh group preview")
    try:
        for index, version_id in enumerate(ids):
            version = await session.get(LegalVersion, version_id)
            if version is None:
                raise LookupError("version missing")
            await approve_legal_version_in_session(
                session,
                _attestation(version, editor.telegram_user_id),
                reviewer=editor,
                require_review_required=True,
                record_rejected_attempt=False,
                idempotency_key=(
                    idempotency_key if index == 0 else uuid5(idempotency_key, str(version_id))
                ),
                request_sha256=request_hash,
                approval_batch_id=idempotency_key,
            )
    except (LegalApprovalRejected, LookupError) as exc:
        raise ApiError(
            status_code=409, code="LEGAL_GROUP_CHANGED", message="Refresh group preview"
        ) from exc
    # The root key, all individual events and all transitions commit together.
    await session.commit()
    return LegalGroupApprovalResponse(
        batchId=idempotency_key, approvedCount=len(ids), versionIds=ids
    )
