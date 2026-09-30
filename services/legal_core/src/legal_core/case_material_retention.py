"""Storage-first retention for isolated short-lived case material objects."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.clinic_document_store import RawCaseMaterialStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExpiredCaseMaterial:
    material_id: UUID
    raw_object_key: str
    deletion_lease_token: UUID


async def _claim_expired_materials(
    session_factory: async_sessionmaker[AsyncSession],
) -> Sequence[ExpiredCaseMaterial]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT material_id, raw_object_key, deletion_lease_token "
                    "FROM public.claim_expired_case_materials()"
                )
            )
        ).mappings().all()
        await session.commit()
    claimed: list[ExpiredCaseMaterial] = []
    for row in rows:
        material_id = row.get("material_id")
        object_key = row.get("raw_object_key")
        lease_token = row.get("deletion_lease_token")
        if (
            not isinstance(material_id, UUID)
            or not isinstance(object_key, str)
            or not isinstance(lease_token, UUID)
        ):
            raise RuntimeError("case-material retention claim returned an invalid row")
        claimed.append(
            ExpiredCaseMaterial(
                material_id=material_id,
                raw_object_key=object_key,
                deletion_lease_token=lease_token,
            )
        )
    return claimed


async def _finish_claim(
    session_factory: async_sessionmaker[AsyncSession],
    material: ExpiredCaseMaterial,
    *,
    deleted: bool,
) -> bool:
    function = (
        "finalize_expired_case_material_deletion"
        if deleted
        else "release_expired_case_material_deletion"
    )
    async with session_factory() as session:
        result = await session.scalar(
            text(
                f"SELECT public.{function}(:material_id, :lease_token)"
            ),
            {
                "material_id": material.material_id,
                "lease_token": material.deletion_lease_token,
            },
        )
        await session.commit()
    return result is True


async def purge_expired_case_materials(
    session_factory: async_sessionmaker[AsyncSession],
    raw_store: RawCaseMaterialStore,
) -> int:
    """Delete raw objects before their metadata lets a parent record be purged.

    The claim/finalize functions make concurrent workers safe without giving this
    process a cross-tenant table scan. No raw bytes, filename or object key enters
    logging on either success or failure.
    """

    deleted_count = 0
    for material in await _claim_expired_materials(session_factory):
        try:
            await raw_store.delete_case_material(stored_object_key=material.raw_object_key)
        except (RuntimeError, ValueError):
            logger.warning("expired case material storage deletion failed; retry is scheduled")
            await _finish_claim(session_factory, material, deleted=False)
            continue
        if await _finish_claim(session_factory, material, deleted=True):
            deleted_count += 1
    return deleted_count
