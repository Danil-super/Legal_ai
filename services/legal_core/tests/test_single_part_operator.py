"""Synthetic-only operator checks; no private source document enters fixtures."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.corpus_loader import CorpusFragment, corpus_fragments_sha256
from legal_core.database import database_url
from legal_core.group_approval import group_preview
from legal_core.material_preparation import MaterialPreparationInput, store_preparation
from legal_core.models import (
    LegalDocument, LegalMaterialPreparation, LegalPreparedPartVersion,
    LegalReviewMaterial, LegalSource, LegalVersion, User,
)
from legal_core.normative_preparation import inspect_normative_rtf
from legal_core.single_part_operator import read_single_part_package, run_single_part_package


def _package(
    root: Path, material_id: UUID, actor_id: UUID, *, part_key: str = "part-1",
) -> Path:
    title = 'Федеральный закон от 1 января 2020 г. N 11-ФЗ "Синтетический"'
    source_url = "https://internet.garant.ru/document/redirect/11111111/0"
    heading = ('{\\field{\\*\\fldinst {HYPERLINK "' + source_url + '"}}'
               '{\\fldrslt {\\cs24 ' + title + '}}}')
    raw = ("{\\rtf1\\ansi\\ansicpg1251\\pard\\plain\\s1\\qc "
           + heading + "\\par}").encode("cp1251")
    normalized = title + "\nСтатья 1. Синтетическая норма только для теста, не действующее право.\n"
    candidate = inspect_normative_rtf(raw, normalized)
    part = candidate.parts[0]
    raw_sha = hashlib.sha256(raw).hexdigest()
    key = f"synthetic-single-part-{uuid4().hex}"
    evidence = {field: f"Synthetic verified locator for {field}" for field in (
        "title", "canonical_key", "document_type", "issuer", "official_number",
        "adoption_date", "publication_date", "version_date", "effective_from",
    )}
    prepared = {
        "raw_sha256": raw_sha, "title": title, "kind": "NORMATIVE",
        "group_key": "general", "parser_version": "synthetic-rtf-v1",
        "source_url": source_url, "source_locator": candidate.source_locator,
        "parts": [{
            "part_key": part_key, "title": title, "canonical_key": key,
            "document_type": "Федеральный закон", "issuer": "Российская Федерация",
            "official_number": "11-ФЗ", "adoption_date": "2020-01-01",
            "publication_date": "2020-01-02", "version_date": "2026-10-01",
            "effective_from": "2020-01-10", "text_start": part.text_start,
            "text_end": part.text_end, "text_sha256": part.text_sha256,
            "evidence": evidence,
        }],
        "extraction_scope": "FULL_DOCUMENT", "normalized_text": normalized,
        "normalized_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        "completeness_locator": "Synthetic complete original comparison",
    }
    fragment = CorpusFragment(
        ordinal=1, article="1", part=None, point=None, heading=None,
        structural_path="Статья 1",
        text="Статья 1. Синтетическая норма только для теста, не действующее право.",
    )
    corpus = {
        "manifest_version": "dental-legal-corpus.v4", "source_key": "garant",
        "source_revision": 1, "source_name": "Гарант",
        "source_trust_level": "VERIFIED_COPY",
        "source_base_url": "https://internet.garant.ru/", "source_url": source_url,
        "source_external_id": "11111111", "allowed_hosts": ["internet.garant.ru"],
        "document_key": key, "document_type": "Федеральный закон",
        "title": title, "issuer": "Российская Федерация",
        "official_number": "11-ФЗ", "adoption_date": "2020-01-01",
        "publication_date": "2020-01-02", "version_date": "2026-10-01",
        "effective_from": "2020-01-10", "effective_to": None,
        "approval_state": "REVIEW_REQUIRED",
        "artifact_kind": "THIRD_PARTY_VERIFIED_COPY",
        "artifact_mime_type": "application/rtf", "artifact_sha256": raw_sha,
        "artifact_path": "original.rtf",
        "artifact_retrieved_at": "2026-10-01T12:00:00Z",
        "artifact_size_bytes": len(raw), "normalized_text": normalized,
        "normalized_sha256": part.text_sha256,
        "fragments_sha256": corpus_fragments_sha256([fragment]),
        "normalization_scope": "FULL_DOCUMENT", "parser_version": "synthetic-rtf-v1",
        "fragments": [fragment.model_dump(mode="json")],
    }
    (root / "original.rtf").write_bytes(raw)
    (root / "corpus.json").write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    operator = {
        "schema_version": 1, "material_id": str(material_id),
        "actor_user_id": str(actor_id), "raw_sha256": raw_sha,
        "part_key": part_key, "part_text_sha256": part.text_sha256,
        "source_url": source_url, "corpus_manifest_path": "corpus.json",
        "preparation": prepared,
    }
    path = root / "operator.json"
    path.write_text(json.dumps(operator, ensure_ascii=False), encoding="utf-8")
    return path


def test_operator_package_requires_exact_parser_match_and_regular_files(tmp_path: Path) -> None:
    path = _package(tmp_path, uuid4(), uuid4())
    package = read_single_part_package(path)
    assert package.preparation.parts[0].text_sha256 == package.corpus.normalized_sha256
    assert package.corpus.approval_state == "REVIEW_REQUIRED"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["part_text_sha256"] = "a" * 64
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="part"):
        read_single_part_package(path)

    payload["part_text_sha256"] = package.preparation.parts[0].text_sha256
    payload["corpus_manifest_path"] = "../corpus.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        read_single_part_package(path)

    path = _package(tmp_path, uuid4(), uuid4())
    (tmp_path / "corpus.json").rename(tmp_path / "actual.json")
    (tmp_path / "corpus.json").symlink_to(tmp_path / "actual.json")
    with pytest.raises(ValueError, match="regular"):
        read_single_part_package(path)


def test_legacy_single_part_key_is_preserved_without_changing_its_digest(tmp_path: Path) -> None:
    path = _package(tmp_path, uuid4(), uuid4(), part_key="document")
    payload = json.loads(path.read_text(encoding="utf-8"))
    before = MaterialPreparationInput.model_validate(payload["preparation"])
    package = read_single_part_package(path)
    assert package.request.part_key == "document"
    assert package.preparation.parts[0].part_key == "document"
    assert package.preparation.digest() == before.digest()
    assert package.preparation.parts[0].text_sha256 == package.preparation.normalized_sha256


@pytest.mark.parametrize("part_key", ["part-2", "other", "document-1"])
def test_single_part_alias_does_not_accept_arbitrary_keys(tmp_path: Path, part_key: str) -> None:
    path = _package(tmp_path, uuid4(), uuid4(), part_key=part_key)
    with pytest.raises(ValueError, match="part"):
        read_single_part_package(path)


def test_operator_rejects_unverified_fields_and_tampered_original(tmp_path: Path) -> None:
    path = _package(tmp_path, uuid4(), uuid4())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["preparation"]["parts"][0]["evidence"].pop("issuer")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence"):
        read_single_part_package(path)

    path = _package(tmp_path, uuid4(), uuid4())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["preparation"]["parts"][0]["evidence"]["issuer"] = "   "
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence"):
        read_single_part_package(path)

    path = _package(tmp_path, uuid4(), uuid4())
    (tmp_path / "original.rtf").write_bytes(b"{\\rtf1 tampered}")
    with pytest.raises(ValueError, match="SHA-256"):
        read_single_part_package(path)

    path = _package(tmp_path, uuid4(), uuid4())
    corpus_path = tmp_path / "corpus.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    corpus["source_revision"] = 2
    corpus_path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="Garant source revision"):
        read_single_part_package(path)


def test_cli_never_prints_supplied_legal_text_on_failure(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    from legal_core import single_part_operator

    path = _package(tmp_path, uuid4(), uuid4())

    async def fail(*_args, **_kwargs):
        raise ValueError("SYNTHETIC_PRIVATE_LEGAL_CONTENT")

    monkeypatch.setattr(single_part_operator, "_run", fail)
    monkeypatch.setattr("sys.argv", ["single_part_operator", str(path)])
    with pytest.raises(SystemExit) as error:
        single_part_operator.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_LEGAL_CONTENT" not in output.out + output.err
    assert "no approval performed" in output.err


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
@pytest.mark.parametrize("part_key", ["part-1", "document"])
def test_single_part_dry_run_rolls_back_and_commit_replays_exactly(
    tmp_path: Path, part_key: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                path = _package(tmp_path, uuid4(), uuid4(), part_key=part_key)
                package = read_single_part_package(path)
                material = LegalReviewMaterial(
                    id=package.request.material_id, package_key=f"pilot-{uuid4().hex}",
                    original_filename="original.rtf", title=package.preparation.title,
                    kind="LEGAL_COPY", source_name="Synthetic source",
                    raw_mime_type="application/rtf", raw_bytes=package.raw_bytes,
                    raw_size_bytes=len(package.raw_bytes),
                    raw_sha256=package.request.raw_sha256, received_at=datetime.now(UTC),
                )
                actor = User(
                    id=package.request.actor_user_id,
                    telegram_user_id=uuid4().int % 10**12 + 1,
                    status="ACTIVE", system_role="LEGAL_EDITOR",
                )
                session.add_all([material, actor])
            cli = subprocess.run(
                [sys.executable, "-m", "legal_core.single_part_operator", str(path)],
                check=True, capture_output=True, text=True, timeout=15,
            )
            assert "dry-run rolled back" in cli.stdout
            assert "no legal approval performed" in cli.stdout
            assert "Синтетическая норма" not in cli.stdout + cli.stderr
            preview = await run_single_part_package(factory, path, commit=False)
            assert preview.committed is False
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(
                    LegalMaterialPreparation
                ).where(LegalMaterialPreparation.material_id == material.id)) == 0
                assert await session.scalar(select(func.count()).select_from(
                    LegalDocument
                ).where(LegalDocument.canonical_key == package.corpus.document_key)) == 0
            first = await run_single_part_package(factory, path, commit=True)
            cli_first = subprocess.run(
                [sys.executable, "-m", "legal_core.single_part_operator", str(path), "--commit"],
                check=True, capture_output=True, text=True, timeout=15,
            )
            cli_replay = subprocess.run(
                [sys.executable, "-m", "legal_core.single_part_operator", str(path), "--commit"],
                check=True, capture_output=True, text=True, timeout=15,
            )
            assert cli_first.stdout == cli_replay.stdout
            assert str(first.preparation_id) in cli_replay.stdout
            assert str(first.version_id) in cli_replay.stdout
            assert str(first.binding_id) in cli_replay.stdout
            replay = await run_single_part_package(factory, path, commit=True)
            assert first.committed and replay.committed
            assert (first.preparation_id, first.version_id, first.binding_id) == (
                replay.preparation_id, replay.version_id, replay.binding_id
            )
            async with factory() as session:
                version = await session.get(LegalVersion, first.version_id)
                assert version.approval_state == "REVIEW_REQUIRED"
                source = await session.get(LegalSource, version.source_id)
                assert source.status in {"DRAFT", "APPROVED"}
                preview = await group_preview(session, "general")
                assert first.version_id in {item.version_id for item in preview.ready}
                assert await session.scalar(select(func.count()).select_from(
                    LegalPreparedPartVersion
                ).where(LegalPreparedPartVersion.material_id == material.id)) == 1
                stored_preparation = await session.get(
                    LegalMaterialPreparation, first.preparation_id,
                )
                assert stored_preparation.metadata_json["parts"][0]["part_key"] == part_key
                assert stored_preparation.preparation_sha256 == package.preparation.digest()
                binding = await session.get(LegalPreparedPartVersion, first.binding_id)
                assert binding.part_key == part_key
                assert await session.scalar(select(func.count()).select_from(
                    LegalMaterialPreparation
                ).where(LegalMaterialPreparation.material_id == material.id)) == 1
                assert await session.scalar(select(func.count()).select_from(
                    LegalVersion
                ).where(LegalVersion.document_id == version.document_id)) == 1
            newer = MaterialPreparationInput.model_validate(
                package.preparation.model_dump()
                | {"completeness_locator": "Synthetic revised completeness evidence"}
            )
            async with factory() as session, session.begin():
                await store_preparation(session, material.id, newer)
            with pytest.raises(ValueError, match="newer preparation"):
                await run_single_part_package(factory, path, commit=True)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
def test_late_authorization_failure_rolls_back_preparation_and_version(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                path = _package(tmp_path, uuid4(), uuid4())
                package = read_single_part_package(path)
                session.add_all([
                    LegalReviewMaterial(
                        id=package.request.material_id, package_key=f"pilot-{uuid4().hex}",
                        original_filename="original.rtf", title=package.preparation.title,
                        kind="LEGAL_COPY", source_name="Synthetic source",
                        raw_mime_type="application/rtf", raw_bytes=package.raw_bytes,
                        raw_size_bytes=len(package.raw_bytes),
                        raw_sha256=package.request.raw_sha256, received_at=datetime.now(UTC),
                    ),
                    User(id=package.request.actor_user_id,
                         telegram_user_id=uuid4().int % 10**12 + 1,
                         status="REVOKED", system_role="LEGAL_EDITOR"),
                ])
            with pytest.raises(PermissionError, match="LEGAL_EDITOR"):
                await run_single_part_package(factory, path, commit=True)
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(
                    LegalMaterialPreparation
                ).where(LegalMaterialPreparation.material_id == package.request.material_id)) == 0
                assert await session.scalar(select(func.count()).select_from(
                    LegalDocument
                ).where(LegalDocument.canonical_key == package.corpus.document_key)) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
