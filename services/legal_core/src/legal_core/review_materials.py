"""Prepare immutable incoming legal-review materials without admitting legal evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.corpus_loader import is_safe_rtf
from legal_core.database import create_engine, create_session_factory
from legal_core.models import LegalReviewMaterial

_MAX_REVIEW_MATERIAL_BYTES = 50_000_000
_PACKAGE_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_GARANT_URL = re.compile(
    rb"https://internet\.garant\.ru/document/redirect/([0-9]{1,20})/0"
)


@dataclass(frozen=True)
class ReviewMaterialInput:
    """One immutable file awaiting legal-editor metadata and approval work."""

    original_filename: str
    title: str
    kind: str
    review_state: str
    source_name: str
    source_url: str | None
    source_external_id: str | None
    mime_type: str
    raw_bytes: bytes
    raw_sha256: str
    received_at: datetime


def collect_review_materials(
    directory: Path, *, received_at: datetime
) -> list[ReviewMaterialInput]:
    """Read an immutable package in deterministic filename order."""

    resolved_directory = directory.resolve()
    if directory.is_symlink() or not resolved_directory.is_dir():
        raise ValueError("review material directory must be a regular directory")
    return [
        review_material_from_path(path, received_at=received_at)
        for path in sorted(resolved_directory.iterdir(), key=lambda item: item.name.casefold())
    ]


def review_material_from_path(path: Path, *, received_at: datetime) -> ReviewMaterialInput:
    """Classify a bounded local input without treating it as approved evidence."""

    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_file():
        raise ValueError("review material must be a regular file")
    if path.name != resolved.name:
        raise ValueError("review material filename is invalid")
    size = resolved.stat().st_size
    if size <= 0 or size > _MAX_REVIEW_MATERIAL_BYTES:
        raise ValueError("review material size is outside the allowed range")
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("review material receipt time must be timezone-aware")

    raw = resolved.read_bytes()
    suffix = resolved.suffix.lower()
    title = resolved.stem.replace("_", " ").strip()
    if not title:
        raise ValueError("review material title is empty")
    if suffix == ".rtf":
        if not is_safe_rtf(raw):
            raise ValueError("review material RTF is unsafe or has an invalid signature")
        matches = _GARANT_URL.findall(raw)
        if len(matches) != 1:
            raise ValueError("review material RTF must contain exactly one Garant source URL")
        external_id = matches[0].decode("ascii")
        return ReviewMaterialInput(
            original_filename=resolved.name,
            title=title,
            kind="LEGAL_COPY",
            review_state="METADATA_REQUIRED",
            source_name="Гарант",
            source_url=f"https://internet.garant.ru/document/redirect/{external_id}/0",
            source_external_id=external_id,
            mime_type="application/rtf",
            raw_bytes=raw,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            received_at=received_at,
        )
    if suffix == ".pdf" and raw.startswith(b"%PDF-"):
        return ReviewMaterialInput(
            original_filename=resolved.name,
            title=title,
            kind="CLINICAL_REFERENCE",
            review_state="METADATA_REQUIRED",
            source_name="Передано юристом; первоисточник не зафиксирован",
            source_url=None,
            source_external_id=None,
            mime_type="application/pdf",
            raw_bytes=raw,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            received_at=received_at,
        )
    raise ValueError("review material type is not supported")


def _same_material(existing: LegalReviewMaterial, candidate: ReviewMaterialInput) -> bool:
    return (
        existing.original_filename == candidate.original_filename
        and existing.title == candidate.title
        and existing.kind == candidate.kind
        and existing.review_state == candidate.review_state
        and existing.source_name == candidate.source_name
        and existing.source_url == candidate.source_url
        and existing.source_external_id == candidate.source_external_id
        and existing.raw_mime_type == candidate.mime_type
        and existing.raw_bytes == candidate.raw_bytes
        and existing.raw_size_bytes == len(candidate.raw_bytes)
        and existing.received_at == candidate.received_at
    )


async def ingest_review_materials(
    session_factory: async_sessionmaker[AsyncSession],
    directory: Path,
    *,
    package_key: str,
    received_at: datetime,
) -> list[UUID]:
    """Persist a whole validated package as metadata-required review material.

    This path intentionally creates no ``LegalVersion`` and cannot make the files retrievable
    by Legal Core. A legal editor must still create and approve a complete evidence version.
    """

    if _PACKAGE_KEY.fullmatch(package_key) is None:
        raise ValueError("review material package key is invalid")
    candidates = collect_review_materials(directory, received_at=received_at)
    if not candidates:
        raise ValueError("review material package is empty")
    material_ids: list[UUID] = []
    async with session_factory() as session, session.begin():
        for candidate in candidates:
            existing = await session.scalar(
                select(LegalReviewMaterial).where(
                    LegalReviewMaterial.package_key == package_key,
                    LegalReviewMaterial.raw_sha256 == candidate.raw_sha256,
                )
            )
            if existing is not None:
                if not _same_material(existing, candidate):
                    raise ValueError("existing review material metadata conflicts with package")
                material_ids.append(existing.id)
                continue
            material = LegalReviewMaterial(
                package_key=package_key,
                original_filename=candidate.original_filename,
                title=candidate.title,
                kind=candidate.kind,
                review_state=candidate.review_state,
                source_name=candidate.source_name,
                source_url=candidate.source_url,
                source_external_id=candidate.source_external_id,
                raw_mime_type=candidate.mime_type,
                raw_sha256=candidate.raw_sha256,
                raw_bytes=candidate.raw_bytes,
                raw_size_bytes=len(candidate.raw_bytes),
                received_at=candidate.received_at,
            )
            session.add(material)
            await session.flush()
            material_ids.append(material.id)
    return material_ids


async def _run(directory: Path, package_key: str) -> None:
    engine = create_engine()
    try:
        material_ids = await ingest_review_materials(
            create_session_factory(engine),
            directory,
            package_key=package_key,
            received_at=datetime.now(UTC),
        )
    finally:
        await engine.dispose()
    print(f"ingested {len(material_ids)} review materials as METADATA_REQUIRED")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest immutable legal review materials")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--package-key", required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.directory, args.package_key))


if __name__ == "__main__":
    main()
