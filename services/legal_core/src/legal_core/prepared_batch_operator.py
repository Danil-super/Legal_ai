"""Atomically prepare complete normative bundles, without any legal approval.

Schema v1 lists package-local, existing part-request files. Each is bound to an
immutable original and evidenced preparation; every declared part must appear.
Default operation is a rolled-back dry run. No fetch, LLM or schema change.
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.contracts import ContractModel
from legal_core.corpus_loader import ingest_manifest_in_session
from legal_core.database import create_engine, create_session_factory
from legal_core.material_preparation import store_preparation
from legal_core.models import LegalMaterialPreparation, LegalReviewMaterial
from legal_core.normative_preparation import bind_prepared_part
from legal_core.preparation_import import PreparationPackageItem, _read_regular
from legal_core.single_part_operator import (
    SinglePartPackage,
    SinglePartResult,
    read_prepared_part_package,
)

_MAX_TOTAL_RAW_BYTES = 80_000_000
_MAX_TOTAL_TEXT_BYTES = 80_000_000


class PreparedBatchRequest(ContractModel):
    schema_version: Literal[1]
    part_requests: list[str] = Field(min_length=1, max_length=200)

    @field_validator("part_requests")
    @classmethod
    def local_filenames(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > 240:
                raise ValueError("batch filename exceeds limit")
            PreparationPackageItem.filename_only(value)
        return values

    @model_validator(mode="after")
    def unique_requests(self) -> "PreparedBatchRequest":
        if len(set(self.part_requests)) != len(self.part_requests):
            raise ValueError("duplicate part request")
        return self


@dataclass(frozen=True)
class PreparedBatch:
    parts: tuple[SinglePartPackage, ...]


@dataclass(frozen=True)
class PreparedBatchResult:
    parts: tuple[SinglePartResult, ...]
    committed: bool


def read_prepared_batch(path: Path) -> PreparedBatch:
    """Reject incomplete coverage, competing revisions and unbounded packages."""
    manifest = PreparedBatchRequest.model_validate_json(
        _read_regular(path.parent, path.name, 256_000)
    )
    parts: list[SinglePartPackage] = []
    originals: dict[UUID, SinglePartPackage] = {}
    seen: set[tuple[UUID, str]] = set()
    raw_bytes = text_bytes = 0
    actor: UUID | None = None
    for name in manifest.part_requests:
        part = read_prepared_part_package(path.parent / name)
        if actor is not None and part.request.actor_user_id != actor:
            raise ValueError("one batch requires the same editor")
        actor = part.request.actor_user_id
        key = (part.request.material_id, part.request.part_key)
        if key in seen:
            raise ValueError("duplicate prepared part")
        seen.add(key)
        previous = originals.get(part.request.material_id)
        if previous is None:
            raw_bytes += len(part.raw_bytes)
            text_bytes += len(part.preparation.normalized_text.encode())
            if raw_bytes > _MAX_TOTAL_RAW_BYTES or text_bytes > _MAX_TOTAL_TEXT_BYTES:
                raise ValueError("batch package exceeds aggregate size limit")
            originals[part.request.material_id] = part
        elif previous.preparation.digest() != part.preparation.digest() or (
            previous.raw_bytes != part.raw_bytes
        ):
            raise ValueError("competing preparation revisions for the same original")
        else:
            # Code bundles repeat the whole preparation in every request. Retain
            # one large text/original after validating equality, not four copies.
            part = replace(
                part, raw_bytes=previous.raw_bytes, preparation=previous.preparation,
                request=part.request.model_copy(update={"preparation": previous.preparation}),
            )
        parts.append(part)
    expected = {
        (material_id, part.part_key)
        for material_id, package in originals.items() for part in package.preparation.parts
    }
    if seen != expected:
        raise ValueError("batch must account for every prepared part of each original")
    return PreparedBatch(tuple(sorted(parts, key=lambda p: (
        p.request.material_id, p.request.part_key,
    ))))


async def run_prepared_batch(
    factory: async_sessionmaker[AsyncSession], path: Path, *, commit: bool = False,
) -> PreparedBatchResult:
    """Caller-visible results commit together; a failure rolls back the whole batch."""
    batch = read_prepared_batch(path)
    async with factory() as session, session.begin() as transaction:
        # Same ordering as the existing receipt importer; protect latest revision
        # checks against concurrent preparation imports for the entire batch.
        material_ids = sorted({part.request.material_id for part in batch.parts})
        locked = list(await session.scalars(select(LegalReviewMaterial.id).where(
            LegalReviewMaterial.id.in_(material_ids)
        ).order_by(LegalReviewMaterial.id).with_for_update()))
        if locked != material_ids:
            raise ValueError("one or more original receipts are missing")
        preparation_ids: dict[UUID, UUID] = {}
        results = []
        for part in batch.parts:
            material_id = part.request.material_id
            preparation_id = preparation_ids.get(material_id)
            if preparation_id is None:
                prepared = await store_preparation(session, material_id, part.preparation)
                latest = await session.scalar(select(func.max(
                    LegalMaterialPreparation.revision
                )).where(LegalMaterialPreparation.material_id == material_id))
                if prepared.revision != latest:
                    raise ValueError("newer preparation revision supersedes this batch")
                preparation_id = prepared.id
                preparation_ids[material_id] = preparation_id
            version_id = await ingest_manifest_in_session(session, part.corpus, part.raw_bytes)
            binding = await bind_prepared_part(
                session, preparation_id=preparation_id, part_key=part.request.part_key,
                legal_version_id=version_id, actor_user_id=part.request.actor_user_id,
            )
            results.append(SinglePartResult(preparation_id, version_id, binding.id, commit))
        result = PreparedBatchResult(tuple(results), commit)
        if not commit:
            await transaction.rollback()
    return result


async def _run(path: Path, commit: bool) -> None:
    engine = create_engine()
    try:
        result = await run_prepared_batch(create_session_factory(engine), path, commit=commit)
    finally:
        await engine.dispose()
    state = "committed" if result.committed else "dry-run rolled back"
    originals = len({part.preparation_id for part in result.parts})
    print(f"{state}: originals={originals} parts={len(result.parts)}; no legal approval")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--commit", action="store_true", help="persist REVIEW_REQUIRED only")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.manifest, args.commit))
    except Exception:
        print("batch preparation failed; no approval performed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
