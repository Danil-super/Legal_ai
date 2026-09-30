"""Storage-first retention for private de-identified reference-evaluation packages."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.clinic_document_store import RawReferenceEvaluationStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExpiredReferenceEvaluationVersion:
    version_id: UUID
    raw_object_key: str | None
    deletion_lease_token: UUID


async def _claim(
    session_factory: async_sessionmaker[AsyncSession],
) -> Sequence[ExpiredReferenceEvaluationVersion]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT version_id, raw_object_key, deletion_lease_token "
                    "FROM public.claim_expired_reference_evaluation_versions()"
                )
            )
        ).mappings().all()
        await session.commit()
    claimed: list[ExpiredReferenceEvaluationVersion] = []
    for row in rows:
        version_id = row.get("version_id")
        raw_object_key = row.get("raw_object_key")
        lease_token = row.get("deletion_lease_token")
        if (
            not isinstance(version_id, UUID)
            or (raw_object_key is not None and not isinstance(raw_object_key, str))
            or not isinstance(lease_token, UUID)
        ):
            raise RuntimeError("reference-evaluation retention claim returned an invalid row")
        claimed.append(
            ExpiredReferenceEvaluationVersion(
                version_id=version_id,
                raw_object_key=raw_object_key,
                deletion_lease_token=lease_token,
            )
        )
    return claimed


async def _finish(
    session_factory: async_sessionmaker[AsyncSession],
    version: ExpiredReferenceEvaluationVersion,
    *,
    deleted: bool,
) -> bool:
    function = (
        "finalize_expired_reference_evaluation_version"
        if deleted
        else "release_expired_reference_evaluation_version"
    )
    async with session_factory() as session:
        result = await session.scalar(
            text(f"SELECT public.{function}(:version_id, :lease_token)"),
            {"version_id": version.version_id, "lease_token": version.deletion_lease_token},
        )
        await session.commit()
    return result is True


async def purge_expired_reference_evaluations(
    session_factory: async_sessionmaker[AsyncSession],
    raw_store: RawReferenceEvaluationStore,
) -> int:
    """Erase optional source object first, then purge its scenario and metadata."""

    deleted_count = 0
    for version in await _claim(session_factory):
        if version.raw_object_key is not None:
            try:
                await raw_store.delete_reference_evaluation(object_key=version.raw_object_key)
            except (RuntimeError, ValueError):
                logger.warning(
                    "expired reference evaluation storage deletion failed; retry is scheduled"
                )
                await _finish(session_factory, version, deleted=False)
                continue
        if await _finish(session_factory, version, deleted=True):
            deleted_count += 1
    return deleted_count
