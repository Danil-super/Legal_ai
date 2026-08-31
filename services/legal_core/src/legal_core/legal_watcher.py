"""Autonomous official-law watcher with a review-only quarantine boundary.

The watcher may discover and download public official PDFs, but it has no database promotion path.
Every staged artifact is immutable, checksum-bound and explicitly marked REVIEW_REQUIRED. A legal
editor must still prepare/verify a corpus candidate and use the existing audited approval flow.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from legal_core.pravo_source import (
    PravoDocumentHit,
    PravoPdfArtifact,
    PravoPublicationClient,
)

WATCH_MANIFEST_VERSION = "dental-legal-watch.v1"
MAX_RULES = 50
MAX_HITS_PER_RULE = 20
PORTAL_PAGE_SIZE = 30


class WatchRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    search_mode: Literal["NAME", "DOCUMENT_TEXT"]
    query: str = Field(min_length=3, max_length=240)
    required_title_terms: list[str] = Field(default_factory=list, max_length=8)
    max_hits: int = Field(default=MAX_HITS_PER_RULE, ge=1, le=MAX_HITS_PER_RULE)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 3:
            raise ValueError("watch query is too short after normalization")
        return normalized

    @field_validator("required_title_terms")
    @classmethod
    def normalize_title_terms(cls, values: list[str]) -> list[str]:
        normalized = [" ".join(value.split()).casefold() for value in values]
        if any(not 2 <= len(value) <= 120 for value in normalized):
            raise ValueError("title terms must contain between 2 and 120 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("title terms must be unique")
        return normalized


class WatchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal["dental-legal-watch.v1"]
    rules: list[WatchRule] = Field(min_length=1, max_length=MAX_RULES)

    @model_validator(mode="after")
    def require_unique_rule_ids(self) -> WatchManifest:
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("watch rule IDs must be unique")
        return self


class PublicationSource(Protocol):
    async def discover(
        self,
        *,
        publication_from: date,
        publication_to: date,
        page: int = 1,
        page_size: int = 100,
        name: str | None = None,
        document_text: str | None = None,
    ) -> tuple[PravoDocumentHit, ...]: ...

    async def fetch_pdf(self, eo_number: str) -> PravoPdfArtifact: ...


@dataclass(frozen=True, slots=True)
class StagedPublication:
    eo_number: str
    pdf_sha256: str
    directory: Path
    created: bool


def load_watch_manifest(path: Path) -> WatchManifest:
    if not path.is_file():
        raise ValueError("watch manifest does not exist")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("watch manifest is not valid UTF-8 JSON") from exc
    return WatchManifest.model_validate(payload)


def _title_matches(rule: WatchRule, title: str) -> bool:
    folded = " ".join(title.split()).casefold()
    return all(term in folded for term in rule.required_title_terms)


def _stable_identity(metadata: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in metadata.items() if key != "stagedAt"}


def _write_once(path: Path, content: bytes) -> bool:
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise FileExistsError(f"refusing to overwrite different quarantine content: {path}")
        return False
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _stage_metadata(
    path: Path,
    *,
    metadata: dict[str, object],
) -> bool:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("existing quarantine metadata is unreadable") from exc
        identity_mismatch = (
            isinstance(existing, dict)
            and _stable_identity(existing) != _stable_identity(metadata)
        )
        if not isinstance(existing, dict) or identity_mismatch:
            raise ValueError("existing quarantine metadata conflicts with the discovered artifact")
        return False
    encoded = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    return _write_once(path, encoded)


async def stage_official_publications(
    source: PublicationSource,
    *,
    manifest: WatchManifest,
    publication_from: date,
    publication_to: date,
    inbox: Path,
    staged_at: datetime,
) -> tuple[StagedPublication, ...]:
    """Discover matching publications and stage immutable PDFs for human legal review."""

    if publication_to < publication_from:
        raise ValueError("publication_to must be on or after publication_from")
    if staged_at.utcoffset() is None:
        raise ValueError("staged_at must contain a UTC offset")

    inbox = inbox.resolve()
    inbox.mkdir(parents=True, exist_ok=True)
    candidates: dict[str, tuple[PravoDocumentHit, set[str]]] = {}

    for rule in manifest.rules:
        hits = await source.discover(
            publication_from=publication_from,
            publication_to=publication_to,
            page=1,
            page_size=PORTAL_PAGE_SIZE,
            name=rule.query if rule.search_mode == "NAME" else None,
            document_text=rule.query if rule.search_mode == "DOCUMENT_TEXT" else None,
        )
        if len(hits) >= PORTAL_PAGE_SIZE:
            raise RuntimeError(
                f"watch rule {rule.rule_id} saturated one portal page; narrow the reviewed query"
            )
        matched = [hit for hit in hits if _title_matches(rule, hit.title)]
        if len(matched) > rule.max_hits:
            raise RuntimeError(f"watch rule {rule.rule_id} exceeded its reviewed hit limit")
        for hit in matched:
            existing = candidates.get(hit.eo_number)
            if existing is None:
                candidates[hit.eo_number] = (hit, {rule.rule_id})
                continue
            stored_hit, rules = existing
            if stored_hit != hit:
                raise ValueError("the source returned conflicting metadata for one EO number")
            rules.add(rule.rule_id)

    receipts: list[StagedPublication] = []
    for eo_number in sorted(candidates):
        hit, rule_ids = candidates[eo_number]
        artifact = await source.fetch_pdf(eo_number)
        if artifact.eo_number != hit.eo_number:
            raise ValueError("downloaded artifact identity does not match discovery metadata")
        if hit.pdf_length not in {None, 0} and len(artifact.content) != hit.pdf_length:
            raise ValueError("downloaded PDF size does not match official discovery metadata")

        directory = inbox / eo_number
        if not directory.resolve().is_relative_to(inbox):
            raise ValueError("quarantine path escaped the configured inbox")
        directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        pdf_path = directory / "official.pdf"
        metadata_path = directory / "candidate.json"
        pdf_created = _write_once(pdf_path, artifact.content)
        metadata = {
            "schemaVersion": WATCH_MANIFEST_VERSION,
            "status": "REVIEW_REQUIRED",
            "autoPromotionAllowed": False,
            "eoNumber": hit.eo_number,
            "title": hit.title,
            "documentNumber": hit.document_number,
            "documentDate": hit.document_date.isoformat() if hit.document_date else None,
            "publicationDate": hit.publication_date.isoformat(),
            "sourceUrl": artifact.source_url,
            "pdfSha256": artifact.sha256,
            "pdfSizeBytes": len(artifact.content),
            "matchedRuleIds": sorted(rule_ids),
            "stagedAt": staged_at.astimezone(UTC).isoformat(),
        }
        metadata_created = _stage_metadata(metadata_path, metadata=metadata)
        receipts.append(
            StagedPublication(
                eo_number=eo_number,
                pdf_sha256=artifact.sha256,
                directory=directory,
                created=pdf_created or metadata_created,
            )
        )
    return tuple(receipts)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


async def _run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Discover official legal publications into a non-promoting review quarantine"
    )
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--inbox", required=True, type=Path)
    parser.add_argument("--publication-from", required=True, type=_iso_date)
    parser.add_argument("--publication-to", required=True, type=_iso_date)
    args = parser.parse_args()

    manifest = load_watch_manifest(args.rules)
    receipts = await stage_official_publications(
        PravoPublicationClient(),
        manifest=manifest,
        publication_from=args.publication_from,
        publication_to=args.publication_to,
        inbox=args.inbox,
        staged_at=datetime.now(UTC),
    )
    created = sum(receipt.created for receipt in receipts)
    print(f"staged {created} new REVIEW_REQUIRED publication(s); total matched {len(receipts)}")


def main() -> None:
    asyncio.run(_run_cli())


if __name__ == "__main__":
    main()
