import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    ingest_manifest,
    load_artifact,
    load_manifest,
    normalized_text_sha256,
)
from legal_core.database import database_url
from legal_core.models import LegalFragment, LegalSource, LegalVersion
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).parents[3]
MANIFEST = ROOT / "services/legal_core/corpus/initial_pp736.json"
PP659_SUBSTANTIVE_SELECTION = (
    ROOT / "services/legal_core/corpus/official/pp659-substantive-selection.json"
)


def test_initial_manifest_is_official_checksum_locked_and_not_auto_approved() -> None:
    manifest = load_manifest(MANIFEST)

    assert manifest.source_url.startswith("https://government.ru/")
    assert manifest.allowed_hosts == ["government.ru"]
    assert manifest.effective_from.isoformat() == "2023-09-01"
    assert manifest.effective_to is not None
    assert manifest.effective_to.isoformat() == "2026-09-01"
    assert manifest.approval_state == "REVIEW_REQUIRED"
    assert manifest.artifact_kind == "NORMALIZED_EXCERPT"
    assert len(manifest.fragments) >= 4


def test_selection_manifest_reuses_the_verified_pp659_raw_artifact() -> None:
    manifest = load_manifest(PP659_SUBSTANTIVE_SELECTION)

    assert manifest.manifest_version == "dental-legal-corpus.v2"
    assert manifest.artifact_kind == "OFFICIAL_RAW"
    assert manifest.official_number == "659"
    assert len(manifest.fragments) >= 10
    assert all(fragment.text in manifest.normalized_content() for fragment in manifest.fragments)


def test_v2_manifest_reads_exact_official_raw_bytes(tmp_path: Path) -> None:
    raw = b"%PDF-1.7\nexact official bytes\n%%EOF\n"
    normalized = "A complete normalized legal document with enough text for validation."
    fragment = CorpusFragment(
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading=None,
        structural_path="point:1",
        text="A complete normalized legal document fragment.",
    )
    artifact = tmp_path / "official.pdf"
    artifact.write_bytes(raw)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "dental-legal-corpus.v2",
                "source_key": "official-test",
                "source_revision": 2,
                "source_name": "Official test source",
                "source_base_url": "https://example.gov.ru/",
                "source_url": "https://example.gov.ru/document/1",
                "source_external_id": "1",
                "allowed_hosts": ["example.gov.ru"],
                "document_key": "test-document",
                "document_type": "DECREE",
                "title": "Test legal document",
                "issuer": "Test authority",
                "official_number": "1",
                "adoption_date": "2026-01-01",
                "publication_date": "2026-01-02",
                "version_date": "2026-01-01",
                "effective_from": "2026-02-01",
                "effective_to": None,
                "approval_state": "REVIEW_REQUIRED",
                "artifact_kind": "OFFICIAL_RAW",
                "artifact_mime_type": "application/pdf",
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                "artifact_path": "official.pdf",
                "artifact_retrieved_at": "2026-08-22T00:00:00Z",
                "artifact_size_bytes": len(raw),
                "artifact_page_count": 1,
                "normalized_text": normalized,
                "normalized_sha256": normalized_text_sha256(normalized),
                "fragments_sha256": corpus_fragments_sha256([fragment]),
                "normalization_scope": "FULL_DOCUMENT",
                "parser_version": "test-parser.v1",
                "fragments": [fragment.model_dump(mode="json")],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert load_artifact(manifest, manifest_path) == raw
    assert manifest.source_revision == 2
    assert manifest.source_base_url == "https://example.gov.ru/"


def test_v2_manifest_rejects_artifact_path_outside_manifest_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7\nnot trusted by path\n%%EOF")
    manifest_path = tmp_path / "manifest.json"
    normalized = "A complete normalized legal document with enough text for validation."
    fragment = CorpusFragment(
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading=None,
        structural_path="point:1",
        text="A complete normalized legal document fragment.",
    )
    payload = {
        "manifest_version": "dental-legal-corpus.v2",
        "source_key": "official-test",
        "source_name": "Official test source",
        "source_url": "https://example.gov.ru/document/1",
        "source_external_id": "1",
        "allowed_hosts": ["example.gov.ru"],
        "document_key": "test-document",
        "document_type": "DECREE",
        "title": "Test legal document",
        "issuer": "Test authority",
        "official_number": "1",
        "adoption_date": "2026-01-01",
        "publication_date": "2026-01-02",
        "version_date": "2026-01-01",
        "effective_from": "2026-02-01",
        "effective_to": None,
        "approval_state": "REVIEW_REQUIRED",
        "artifact_kind": "OFFICIAL_RAW",
        "artifact_mime_type": "application/pdf",
        "artifact_sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
        "artifact_path": "../outside.pdf",
        "artifact_retrieved_at": "2026-08-22T00:00:00Z",
        "artifact_size_bytes": len(outside.read_bytes()),
        "artifact_page_count": 1,
        "normalized_text": normalized,
        "normalized_sha256": normalized_text_sha256(normalized),
        "fragments_sha256": corpus_fragments_sha256([fragment]),
        "normalization_scope": "FULL_DOCUMENT",
        "parser_version": "test-parser.v1",
        "fragments": [fragment.model_dump(mode="json")],
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="inside the manifest directory"):
        load_manifest(manifest_path)


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL corpus tests",
)
def test_manifest_ingestion_is_idempotent_and_stays_review_required(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            first = await ingest_manifest(factory, MANIFEST)
            second = await ingest_manifest(factory, MANIFEST)
            assert first == second
            conflicting_payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
            conflicting_payload["parser_version"] = "conflicting-parser.v2"
            conflicting_manifest = tmp_path / "conflicting.json"
            conflicting_manifest.write_text(
                json.dumps(conflicting_payload, ensure_ascii=False), encoding="utf-8"
            )
            with pytest.raises(ValueError, match="metadata conflicts"):
                await ingest_manifest(factory, conflicting_manifest)
            async with factory() as session:
                version = await session.get(LegalVersion, first)
                assert version is not None
                assert version.approval_state == "REVIEW_REQUIRED"
                assert version.regression_passed is False
                fragment_count = await session.scalar(
                    select(func.count(LegalFragment.id)).where(LegalFragment.version_id == first)
                )
                assert fragment_count == 5
                source = await session.scalar(
                    select(LegalSource).where(LegalSource.source_key == "government-ru")
                )
                assert source is not None
                assert source.status == "DRAFT"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL corpus tests",
)
def test_changed_fragment_selection_creates_a_new_review_revision(tmp_path: Path) -> None:
    raw = b"%PDF-1.7\nimmutable official bytes\n%%EOF\n"
    normalized = "Official text. First verified fragment. Second verified fragment."
    first_fragment = CorpusFragment(
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading="First",
        structural_path="Rule/1",
        text="First verified fragment.",
    )
    second_fragment = CorpusFragment(
        ordinal=2,
        article=None,
        part=None,
        point="2",
        heading="Second",
        structural_path="Rule/2",
        text="Second verified fragment.",
    )
    (tmp_path / "official.pdf").write_bytes(raw)
    base = {
        "manifest_version": "dental-legal-corpus.v2",
        "source_key": "selection-revision-test",
        "source_name": "Selection revision test source",
        "source_url": "https://example.gov.ru/document/selection-revision",
        "source_external_id": "selection-revision",
        "allowed_hosts": ["example.gov.ru"],
        "document_key": "selection-revision-document",
        "document_type": "DECREE",
        "title": "Selection revision legal document",
        "issuer": "Integration authority",
        "official_number": "selection-revision-1",
        "adoption_date": "2026-01-01",
        "publication_date": "2026-01-02",
        "version_date": "2026-01-01",
        "effective_from": "2026-02-01",
        "effective_to": "2027-02-01",
        "approval_state": "REVIEW_REQUIRED",
        "artifact_kind": "OFFICIAL_RAW",
        "artifact_mime_type": "application/pdf",
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "artifact_path": "official.pdf",
        "artifact_retrieved_at": "2026-08-22T00:00:00Z",
        "artifact_size_bytes": len(raw),
        "artifact_page_count": 1,
        "normalized_text": normalized,
        "normalized_sha256": normalized_text_sha256(normalized),
        "normalization_scope": "FULL_DOCUMENT",
        "parser_version": "selection-revision.v1",
    }

    def write_manifest(name: str, fragments: list[CorpusFragment]) -> Path:
        payload = {
            **base,
            "fragments_sha256": corpus_fragments_sha256(fragments),
            "fragments": [fragment.model_dump(mode="json") for fragment in fragments],
        }
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    first_manifest = write_manifest("first.json", [first_fragment])
    expanded_manifest = write_manifest("expanded.json", [first_fragment, second_fragment])

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            first = await ingest_manifest(factory, first_manifest)
            expanded = await ingest_manifest(factory, expanded_manifest)
            assert first != expanded
            assert await ingest_manifest(factory, expanded_manifest) == expanded
            async with factory() as session:
                revisions = list(
                    await session.scalars(
                        select(LegalVersion)
                        .where(LegalVersion.raw_sha256 == hashlib.sha256(raw).hexdigest())
                        .order_by(LegalVersion.version_no)
                    )
                )
                assert [revision.version_no for revision in revisions] == [1, 2]
                assert all(revision.approval_state == "REVIEW_REQUIRED" for revision in revisions)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
