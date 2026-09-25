"""Prepare immutable incoming legal-review materials without admitting legal evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from legal_core.corpus_loader import is_safe_rtf

_MAX_REVIEW_MATERIAL_BYTES = 50_000_000
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
