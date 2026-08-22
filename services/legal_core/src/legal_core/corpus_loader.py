"""Deterministic, non-approving loader for reviewed legal corpus manifests."""

import argparse
import asyncio
import hashlib
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.database import create_engine, create_session_factory
from legal_core.models import LegalDocument, LegalFragment, LegalSource, LegalVersion


class CorpusFragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(ge=1)
    article: str | None
    part: str | None
    point: str | None
    heading: str | None
    structural_path: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=20, max_length=10_000)


class CorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal["dental-legal-corpus.v1"]
    source_key: str
    source_name: str
    source_url: str
    source_external_id: str
    allowed_hosts: list[str] = Field(min_length=1)
    document_key: str
    document_type: str
    title: str
    issuer: str
    official_number: str
    adoption_date: date
    publication_date: date
    version_date: date
    effective_from: date
    effective_to: date | None
    approval_state: Literal["REVIEW_REQUIRED"]
    artifact_mime_type: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_text: str = Field(min_length=100)
    fragments: list[CorpusFragment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_and_checksum(self) -> "CorpusManifest":
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise ValueError("source URL must be HTTPS and match the manifest allowlist")
        actual_sha = hashlib.sha256(self.artifact_text.encode()).hexdigest()
        if actual_sha != self.artifact_sha256:
            raise ValueError("artifact SHA-256 does not match the manifest")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be later than effective_from")
        ordinals = [fragment.ordinal for fragment in self.fragments]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("fragment ordinals must be unique")
        return self


def load_manifest(path: Path) -> CorpusManifest:
    return CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))


async def _source(session: AsyncSession, manifest: CorpusManifest) -> LegalSource:
    source = await session.scalar(
        select(LegalSource).where(
            LegalSource.source_key == manifest.source_key,
            LegalSource.revision == 1,
        )
    )
    if source is not None:
        return source
    source = LegalSource(
        source_key=manifest.source_key,
        revision=1,
        display_name=manifest.source_name,
        base_url=manifest.source_url,
        allowed_hosts=manifest.allowed_hosts,
        trust_level="PRIMARY",
        # Loader never approves a source. Legal-editor review is a separate audited action.
        status="DRAFT",
    )
    session.add(source)
    await session.flush()
    return source


async def _document(session: AsyncSession, manifest: CorpusManifest) -> LegalDocument:
    document = await session.scalar(
        select(LegalDocument).where(LegalDocument.canonical_key == manifest.document_key)
    )
    if document is not None:
        return document
    document = LegalDocument(
        canonical_key=manifest.document_key,
        jurisdiction="RU",
        document_type=manifest.document_type,
        title=manifest.title,
        issuer=manifest.issuer,
        official_number=manifest.official_number,
        adoption_date=manifest.adoption_date,
    )
    session.add(document)
    await session.flush()
    return document


async def ingest_manifest(session_factory: async_sessionmaker[AsyncSession], path: Path) -> UUID:
    manifest = load_manifest(path)
    async with session_factory() as session:
        async with session.begin():
            source = await _source(session, manifest)
            document = await _document(session, manifest)
            existing = await session.scalar(
                select(LegalVersion).where(
                    LegalVersion.document_id == document.id,
                    LegalVersion.raw_sha256 == manifest.artifact_sha256,
                )
            )
            if existing is not None:
                return existing.id
            current_version = await session.scalar(
                select(func.coalesce(func.max(LegalVersion.version_no), 0)).where(
                    LegalVersion.document_id == document.id
                )
            )
            version = LegalVersion(
                document_id=document.id,
                source_id=source.id,
                version_no=int(current_version or 0) + 1,
                source_external_id=manifest.source_external_id,
                source_url=manifest.source_url,
                publication_date=manifest.publication_date,
                version_date=manifest.version_date,
                effective_from=manifest.effective_from,
                effective_to=manifest.effective_to,
                approval_state="REVIEW_REQUIRED",
                raw_sha256=manifest.artifact_sha256,
                raw_mime_type=manifest.artifact_mime_type,
                raw_bytes=manifest.artifact_text.encode(),
                normalized_text=manifest.artifact_text,
                parser_version="manual-official-excerpt.v1",
                regression_passed=False,
            )
            session.add(version)
            await session.flush()
            session.add_all(
                [
                    LegalFragment(
                        version_id=version.id,
                        ordinal=fragment.ordinal,
                        article=fragment.article,
                        part=fragment.part,
                        point=fragment.point,
                        heading=fragment.heading,
                        structural_path=fragment.structural_path,
                        fragment_text=fragment.text,
                        text_sha256=hashlib.sha256(fragment.text.encode()).hexdigest(),
                    )
                    for fragment in manifest.fragments
                ]
            )
        return version.id


async def _run(path: Path) -> None:
    engine = create_engine()
    try:
        identifier = await ingest_manifest(create_session_factory(engine), path)
        print(f"ingested legal version {identifier} as REVIEW_REQUIRED")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a checksum-locked corpus manifest")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    asyncio.run(_run(args.manifest))


if __name__ == "__main__":
    main()
