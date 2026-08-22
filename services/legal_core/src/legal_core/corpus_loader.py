"""Deterministic, non-approving loader for reviewed legal corpus manifests."""

import argparse
import asyncio
import hashlib
from datetime import date, datetime
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


def normalized_text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def corpus_fragments_sha256(fragments: list[CorpusFragment]) -> str:
    canonical = "\n".join(
        f"{fragment.ordinal}:{hashlib.sha256(fragment.text.encode()).hexdigest()}"
        for fragment in sorted(fragments, key=lambda item: item.ordinal)
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class CorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal["dental-legal-corpus.v1", "dental-legal-corpus.v2"]
    source_key: str
    source_revision: int = Field(default=1, ge=1)
    source_name: str
    source_base_url: str | None = None
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
    artifact_kind: Literal["NORMALIZED_EXCERPT", "OFFICIAL_RAW"] = "NORMALIZED_EXCERPT"
    artifact_mime_type: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_text: str | None = Field(default=None, min_length=100)
    artifact_path: str | None = Field(default=None, min_length=1, max_length=240)
    artifact_retrieved_at: datetime | None = None
    artifact_size_bytes: int | None = Field(default=None, gt=0, le=50_000_000)
    artifact_page_count: int | None = Field(default=None, gt=0, le=10_000)
    normalized_text: str | None = Field(default=None, min_length=50)
    normalized_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fragments_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalization_scope: Literal["SELECTED_EXCERPT", "FULL_DOCUMENT"] = "SELECTED_EXCERPT"
    parser_version: str = Field(default="manual-official-excerpt.v1", min_length=1, max_length=80)
    fragments: list[CorpusFragment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_and_checksum(self) -> "CorpusManifest":
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise ValueError("source URL must be HTTPS and match the manifest allowlist")
        base_url = self.source_base_url or self.source_url
        parsed_base = urlparse(base_url)
        if parsed_base.scheme != "https" or parsed_base.hostname not in self.allowed_hosts:
            raise ValueError("source base URL must be HTTPS and match the manifest allowlist")
        if self.manifest_version == "dental-legal-corpus.v1":
            if self.artifact_kind != "NORMALIZED_EXCERPT" or self.artifact_text is None:
                raise ValueError("v1 manifests must contain a normalized excerpt")
            if self.artifact_path is not None or self.normalized_text is not None:
                raise ValueError("v1 manifests cannot reference external artifacts")
            if self.normalization_scope != "SELECTED_EXCERPT":
                raise ValueError("v1 manifests must declare SELECTED_EXCERPT scope")
        else:
            if self.artifact_kind != "OFFICIAL_RAW":
                raise ValueError("v2 manifests must identify an OFFICIAL_RAW artifact")
            if self.artifact_path is None or self.normalized_text is None:
                raise ValueError("v2 manifests require artifact_path and normalized_text")
            if self.artifact_text is not None:
                raise ValueError("v2 manifests cannot embed artifact_text")
            if (
                self.artifact_retrieved_at is None
                or self.artifact_retrieved_at.utcoffset() is None
                or self.artifact_size_bytes is None
                or self.normalized_sha256 is None
                or self.fragments_sha256 is None
                or self.normalization_scope != "FULL_DOCUMENT"
            ):
                raise ValueError(
                    "v2 manifests require complete retrieval and normalization metadata"
                )
            if self.artifact_mime_type == "application/pdf" and self.artifact_page_count is None:
                raise ValueError("v2 PDF manifests require artifact_page_count")
            if normalized_text_sha256(self.normalized_text) != self.normalized_sha256:
                raise ValueError("normalized text SHA-256 does not match the manifest")
            if corpus_fragments_sha256(self.fragments) != self.fragments_sha256:
                raise ValueError("fragment aggregate SHA-256 does not match the manifest")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be later than effective_from")
        ordinals = [fragment.ordinal for fragment in self.fragments]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("fragment ordinals must be unique")
        return self

    def normalized_content(self) -> str:
        content = self.normalized_text if self.normalized_text is not None else self.artifact_text
        if content is None:  # pragma: no cover - protected by manifest validation
            raise ValueError("normalized legal text is missing")
        return content


def load_artifact(manifest: CorpusManifest, manifest_path: Path) -> bytes:
    if manifest.artifact_text is not None:
        raw_bytes = manifest.artifact_text.encode()
    else:
        if manifest.artifact_path is None:  # pragma: no cover - protected by validation
            raise ValueError("artifact_path is missing")
        manifest_directory = manifest_path.resolve().parent
        artifact_path = (manifest_directory / manifest.artifact_path).resolve()
        if not artifact_path.is_relative_to(manifest_directory):
            raise ValueError("artifact must be inside the manifest directory")
        if not artifact_path.is_file():
            raise ValueError("artifact file does not exist")
        raw_bytes = artifact_path.read_bytes()

    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha != manifest.artifact_sha256:
        raise ValueError("artifact SHA-256 does not match the manifest")
    if manifest.artifact_size_bytes is not None and len(raw_bytes) != manifest.artifact_size_bytes:
        raise ValueError("artifact byte count does not match the manifest")
    if manifest.artifact_kind == "OFFICIAL_RAW":
        if manifest.artifact_mime_type == "application/pdf" and not raw_bytes.startswith(b"%PDF-"):
            raise ValueError("official PDF artifact has an invalid signature")
        if not raw_bytes:
            raise ValueError("official raw artifact is empty")
    return raw_bytes


def load_manifest(path: Path) -> CorpusManifest:
    manifest = CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))
    load_artifact(manifest, path)
    return manifest


async def _source(session: AsyncSession, manifest: CorpusManifest) -> LegalSource:
    source = await session.scalar(
        select(LegalSource).where(
            LegalSource.source_key == manifest.source_key,
            LegalSource.revision == manifest.source_revision,
        )
    )
    source_base_url = manifest.source_base_url or manifest.source_url
    if source is not None:
        identity = (
            source.display_name,
            source.base_url,
            source.allowed_hosts,
            source.trust_level,
        )
        expected = (
            manifest.source_name,
            source_base_url,
            manifest.allowed_hosts,
            "PRIMARY",
        )
        if identity != expected:
            raise ValueError("existing legal source metadata conflicts with the manifest")
        return source
    source = LegalSource(
        source_key=manifest.source_key,
        revision=manifest.source_revision,
        display_name=manifest.source_name,
        base_url=source_base_url,
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
        identity = (
            document.jurisdiction,
            document.document_type,
            document.title,
            document.issuer,
            document.official_number,
            document.adoption_date,
        )
        expected = (
            "RU",
            manifest.document_type,
            manifest.title,
            manifest.issuer,
            manifest.official_number,
            manifest.adoption_date,
        )
        if identity != expected:
            raise ValueError("existing legal document metadata conflicts with the manifest")
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
    raw_bytes = load_artifact(manifest, path)
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
                expected_version = (
                    source.id,
                    manifest.source_external_id,
                    manifest.source_url,
                    manifest.publication_date,
                    manifest.version_date,
                    manifest.effective_from,
                    manifest.effective_to,
                    manifest.artifact_kind,
                    manifest.artifact_mime_type,
                    raw_bytes,
                    manifest.normalized_content(),
                    manifest.parser_version,
                    len(raw_bytes),
                    normalized_text_sha256(manifest.normalized_content()),
                    corpus_fragments_sha256(manifest.fragments),
                    manifest.normalization_scope,
                    manifest.artifact_retrieved_at,
                    manifest.artifact_page_count,
                )
                stored_version = (
                    existing.source_id,
                    existing.source_external_id,
                    existing.source_url,
                    existing.publication_date,
                    existing.version_date,
                    existing.effective_from,
                    existing.effective_to,
                    existing.artifact_kind,
                    existing.raw_mime_type,
                    existing.raw_bytes,
                    existing.normalized_text,
                    existing.parser_version,
                    existing.raw_size_bytes,
                    existing.normalized_sha256,
                    existing.fragments_sha256,
                    existing.normalization_scope,
                    existing.artifact_retrieved_at,
                    existing.artifact_page_count,
                )
                stored_fragments = list(
                    (
                        await session.scalars(
                            select(LegalFragment)
                            .where(LegalFragment.version_id == existing.id)
                            .order_by(LegalFragment.ordinal)
                        )
                    ).all()
                )
                stored_fragment_models = [
                    CorpusFragment(
                        ordinal=fragment.ordinal,
                        article=fragment.article,
                        part=fragment.part,
                        point=fragment.point,
                        heading=fragment.heading,
                        structural_path=fragment.structural_path,
                        text=fragment.fragment_text,
                    )
                    for fragment in stored_fragments
                ]
                if (
                    stored_version != expected_version
                    or stored_fragment_models != manifest.fragments
                ):
                    raise ValueError("existing legal version metadata conflicts with the manifest")
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
                artifact_kind=manifest.artifact_kind,
                raw_sha256=manifest.artifact_sha256,
                raw_mime_type=manifest.artifact_mime_type,
                raw_bytes=raw_bytes,
                normalized_text=manifest.normalized_content(),
                parser_version=manifest.parser_version,
                raw_size_bytes=len(raw_bytes),
                normalized_sha256=normalized_text_sha256(manifest.normalized_content()),
                fragments_sha256=corpus_fragments_sha256(manifest.fragments),
                normalization_scope=manifest.normalization_scope,
                artifact_retrieved_at=manifest.artifact_retrieved_at,
                artifact_page_count=manifest.artifact_page_count,
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
