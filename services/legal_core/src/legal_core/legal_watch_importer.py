"""Import immutable watcher quarantine receipts into a DB review queue.

The network-facing watcher intentionally has no database access. This importer runs on the backend
network, mounts the watcher inbox read-only, revalidates every candidate and records only a
pre-classification REVIEW_REQUIRED discovery. It cannot create or approve LegalVersion rows.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.database import create_engine, create_session_factory
from legal_core.legal_watcher import WATCH_MANIFEST_VERSION
from legal_core.pravo_source import MAX_PDF_BYTES, PRAVO_HOST

MAX_CANDIDATE_JSON_BYTES = 64 * 1024
MAX_IMPORT_CANDIDATES = 500
_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


class WatchCandidateMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["dental-legal-watch.v1"] = Field(alias="schemaVersion")
    status: Literal["REVIEW_REQUIRED"]
    auto_promotion_allowed: Literal[False] = Field(alias="autoPromotionAllowed")
    eo_number: str = Field(alias="eoNumber", pattern=r"^[0-9]{16,24}$")
    title: str = Field(min_length=1, max_length=4000)
    document_number: str | None = Field(default=None, alias="documentNumber", max_length=120)
    document_date: date | None = Field(default=None, alias="documentDate")
    publication_date: date = Field(alias="publicationDate")
    source_url: str = Field(alias="sourceUrl", min_length=1, max_length=2000)
    pdf_sha256: str = Field(alias="pdfSha256", pattern=r"^[0-9a-f]{64}$")
    pdf_size_bytes: int = Field(alias="pdfSizeBytes", ge=5, le=MAX_PDF_BYTES)
    matched_rule_ids: list[str] = Field(alias="matchedRuleIds", min_length=1, max_length=50)
    staged_at: datetime = Field(alias="stagedAt")

    @field_validator("matched_rule_ids")
    @classmethod
    def validate_rule_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("matched rule IDs must be unique")
        if any(_RULE_ID.fullmatch(value) is None for value in values):
            raise ValueError("matched rule ID is malformed")
        return values

    @model_validator(mode="after")
    def validate_source_identity(self) -> WatchCandidateMetadata:
        if self.schema_version != WATCH_MANIFEST_VERSION:
            raise ValueError("unsupported watcher manifest version")
        if self.staged_at.utcoffset() is None:
            raise ValueError("stagedAt must include a UTC offset")
        parsed = urlparse(self.source_url)
        try:
            query = parse_qs(parsed.query, strict_parsing=True)
        except ValueError as exc:
            raise ValueError("candidate source URL has a malformed query") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname != PRAVO_HOST
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.path.casefold() != "/file/pdf"
            or query.get("eoNumber") != [self.eo_number]
            or set(query) != {"eoNumber"}
        ):
            raise ValueError("candidate source URL does not match the official PDF identity")
        return self

    def stable_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json", by_alias=True)
        payload.pop("stagedAt", None)
        return payload


@dataclass(frozen=True, slots=True)
class ValidatedWatchCandidate:
    metadata: WatchCandidateMetadata
    quarantine_ref: str
    candidate_sha256: str


@dataclass(frozen=True, slots=True)
class WatchImportStats:
    scanned: int
    imported: int
    existing: int


def _canonical_sha256(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_staged_candidate(*, inbox: Path, directory: Path) -> ValidatedWatchCandidate:
    inbox = inbox.resolve()
    if directory.is_symlink():
        raise ValueError("candidate path must not be a symlink")
    directory = directory.resolve()
    if not directory.is_relative_to(inbox) or directory.parent != inbox:
        raise ValueError("candidate directory escaped the configured inbox")
    if not directory.is_dir():
        raise ValueError("candidate path must be a real directory")

    metadata_path = directory / "candidate.json"
    pdf_path = directory / "official.pdf"
    if metadata_path.is_symlink() or pdf_path.is_symlink():
        raise ValueError("watch quarantine files must not be symlinks")
    if not metadata_path.is_file() or not pdf_path.is_file():
        raise ValueError("watch quarantine candidate is incomplete")
    if metadata_path.stat().st_size > MAX_CANDIDATE_JSON_BYTES:
        raise ValueError("watch quarantine metadata exceeds the size limit")

    try:
        raw_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("watch quarantine metadata is not valid UTF-8 JSON") from exc
    metadata = WatchCandidateMetadata.model_validate(raw_metadata)
    if directory.name != metadata.eo_number:
        raise ValueError("candidate directory name does not match the EO number")

    pdf_size = pdf_path.stat().st_size
    if pdf_size != metadata.pdf_size_bytes or not 5 <= pdf_size <= MAX_PDF_BYTES:
        raise ValueError("quarantine PDF size does not match candidate metadata")
    pdf = pdf_path.read_bytes()
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("quarantine artifact does not have a PDF signature")
    if hashlib.sha256(pdf).hexdigest() != metadata.pdf_sha256:
        raise ValueError("quarantine PDF SHA-256 does not match candidate metadata")

    quarantine_ref = f"{metadata.eo_number}/official.pdf"
    stable_payload = metadata.stable_payload()
    stable_payload["quarantineRef"] = quarantine_ref
    return ValidatedWatchCandidate(
        metadata=metadata,
        quarantine_ref=quarantine_ref,
        candidate_sha256=_canonical_sha256(stable_payload),
    )


_SELECT_EXISTING = text(
    """
    SELECT id, candidate_sha256, pdf_sha256, quarantine_ref
      FROM legal_watch_discoveries
     WHERE eo_number = :eo_number
    """
)

_INSERT_DISCOVERY = text(
    """
    INSERT INTO legal_watch_discoveries (
        eo_number,
        title,
        document_number,
        document_date,
        publication_date,
        source_url,
        pdf_sha256,
        pdf_size_bytes,
        matched_rule_ids_json,
        quarantine_ref,
        candidate_sha256,
        status,
        staged_at
    )
    VALUES (
        :eo_number,
        :title,
        :document_number,
        :document_date,
        :publication_date,
        :source_url,
        :pdf_sha256,
        :pdf_size_bytes,
        CAST(:matched_rule_ids_json AS jsonb),
        :quarantine_ref,
        :candidate_sha256,
        'REVIEW_REQUIRED',
        :staged_at
    )
    ON CONFLICT (eo_number) DO NOTHING
    RETURNING id
    """
)


async def _record_candidate(
    session: AsyncSession,
    candidate: ValidatedWatchCandidate,
) -> bool:
    metadata = candidate.metadata
    inserted = await session.scalar(
        _INSERT_DISCOVERY,
        {
            "eo_number": metadata.eo_number,
            "title": metadata.title,
            "document_number": metadata.document_number,
            "document_date": metadata.document_date,
            "publication_date": metadata.publication_date,
            "source_url": metadata.source_url,
            "pdf_sha256": metadata.pdf_sha256,
            "pdf_size_bytes": metadata.pdf_size_bytes,
            "matched_rule_ids_json": json.dumps(
                metadata.matched_rule_ids,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "quarantine_ref": candidate.quarantine_ref,
            "candidate_sha256": candidate.candidate_sha256,
            "staged_at": metadata.staged_at,
        },
    )
    if isinstance(inserted, UUID):
        return True

    existing = (
        await session.execute(_SELECT_EXISTING, {"eo_number": metadata.eo_number})
    ).mappings().one()
    if (
        existing["candidate_sha256"] != candidate.candidate_sha256
        or existing["pdf_sha256"] != metadata.pdf_sha256
        or existing["quarantine_ref"] != candidate.quarantine_ref
    ):
        raise RuntimeError("existing legal watch discovery conflicts with quarantine content")
    return False


async def import_watch_inbox(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    inbox: Path,
    max_candidates: int = 100,
) -> WatchImportStats:
    if not 1 <= max_candidates <= MAX_IMPORT_CANDIDATES:
        raise ValueError(f"max_candidates must be between 1 and {MAX_IMPORT_CANDIDATES}")
    inbox = inbox.resolve()
    if not inbox.is_dir():
        raise ValueError("legal watch inbox does not exist")

    directories = sorted(
        (path for path in inbox.iterdir() if path.is_dir() and not path.is_symlink()),
        key=lambda path: path.name,
    )[:max_candidates]
    imported = 0
    existing_count = 0
    for directory in directories:
        candidate = load_staged_candidate(inbox=inbox, directory=directory)
        async with session_factory() as session, session.begin():
            created = await _record_candidate(session, candidate)
        if created:
            imported += 1
        else:
            existing_count += 1
    return WatchImportStats(
        scanned=len(directories),
        imported=imported,
        existing=existing_count,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import validated legal watcher quarantine items into REVIEW_REQUIRED DB queue"
    )
    parser.add_argument("--inbox", required=True, type=Path)
    parser.add_argument("--max-candidates", type=int, default=100)
    return parser


async def _run_cli() -> None:
    args = _parser().parse_args()
    engine = create_engine()
    factory = create_session_factory(engine)
    try:
        stats = await import_watch_inbox(
            factory,
            inbox=args.inbox,
            max_candidates=args.max_candidates,
        )
        print(
            f"watch review queue: scanned={stats.scanned} imported={stats.imported} "
            f"existing={stats.existing}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run_cli())


if __name__ == "__main__":
    main()
