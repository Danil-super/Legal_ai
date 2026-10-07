"""Synthetic-only complete bundle preparation, never production legal approval."""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.database import database_url
from legal_core.group_approval import group_preview
from legal_core.models import (
    LegalApprovalEvent, LegalDocument, LegalMaterialPreparation, LegalPreparedPartVersion,
    LegalReviewMaterial, LegalVersion, User,
)
from legal_core.prepared_batch_operator import read_prepared_batch, run_prepared_batch
from legal_core.single_part_operator import read_single_part_package
from test_prepared_material_versions import _bundle_input, _raw, _write_manifest
from test_single_part_operator import _package


def _batch(root: Path, *, complete: bool = True) -> Path:
    prepared = _bundle_input()
    material_id, actor_id = uuid4(), uuid4()
    names = []
    for index, part in enumerate(prepared.parts):
        corpus = _write_manifest(root, prepared, index)
        request = {
            "schema_version": 1, "material_id": str(material_id),
            "actor_user_id": str(actor_id), "raw_sha256": prepared.raw_sha256,
            "part_key": part.part_key, "part_text_sha256": part.text_sha256,
            "source_url": prepared.source_url, "corpus_manifest_path": corpus.name,
            "preparation": prepared.model_dump(mode="json"),
        }
        path = root / f"operator-{index + 1}.json"
        path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        names.append(path.name)
    manifest = root / "batch.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "part_requests": names if complete else names[:1],
    }), encoding="utf-8")
    return manifest


def test_complete_bundle_can_be_prepared_in_one_batch(tmp_path: Path) -> None:
    path = _batch(tmp_path)
    batch = read_prepared_batch(path)
    assert len(batch.parts) == 2
    assert len({part.request.material_id for part in batch.parts}) == 1
    assert len({part.preparation.digest() for part in batch.parts}) == 1
    assert batch.parts[0].preparation is batch.parts[1].preparation
    assert batch.parts[0].raw_bytes is batch.parts[1].raw_bytes
    assert batch.parts[1].request.preparation is batch.parts[0].preparation
    assert all(part.corpus.approval_state == "REVIEW_REQUIRED" for part in batch.parts)
    assert batch.parts[0].corpus.normalized_text != batch.parts[1].corpus.normalized_text
    # The existing one-part CLI remains a one-part contract.
    with pytest.raises(ValueError, match="one-part"):
        read_single_part_package(tmp_path / "operator-1.json")


def test_existing_single_part_requests_are_batch_compatible(tmp_path: Path) -> None:
    operator = _package(tmp_path, uuid4(), uuid4(), part_key="document")
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"schema_version": 1, "part_requests": [operator.name]}))
    batch = read_prepared_batch(path)
    assert len(batch.parts) == 1 and batch.parts[0].request.part_key == "document"


@pytest.mark.parametrize("bound", ["raw", "text"])
def test_batch_enforces_total_size_by_unique_original(tmp_path, monkeypatch, bound) -> None:
    from legal_core import prepared_batch_operator

    path = _batch(tmp_path)
    batch = read_prepared_batch(path)
    first = batch.parts[0]
    name, size = ("_MAX_TOTAL_RAW_BYTES", len(first.raw_bytes)) if bound == "raw" else (
        "_MAX_TOTAL_TEXT_BYTES", len(first.preparation.normalized_text.encode())
    )
    monkeypatch.setattr(prepared_batch_operator, name, size)
    assert len(read_prepared_batch(path).parts) == 2
    monkeypatch.setattr(prepared_batch_operator, name, size - 1)
    with pytest.raises(ValueError, match="aggregate size"):
        read_prepared_batch(path)


def test_batch_cannot_hide_an_unprepared_code_part(tmp_path: Path) -> None:
    path = _batch(tmp_path, complete=False)
    with pytest.raises(ValueError, match="every prepared part"):
        read_prepared_batch(path)


@pytest.mark.parametrize("defect", ["duplicate", "traversal", "symlink", "actor", "revision"])
def test_batch_rejects_ambiguous_or_unsafe_input(tmp_path: Path, defect: str) -> None:
    path = _batch(tmp_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if defect == "duplicate":
        manifest["part_requests"].append(manifest["part_requests"][0])
    elif defect == "traversal":
        manifest["part_requests"][0] = "../operator-1.json"
    elif defect == "symlink":
        original = tmp_path / "operator-1.json"
        original.rename(tmp_path / "actual.json")
        original.symlink_to(tmp_path / "actual.json")
    else:
        request_path = tmp_path / "operator-2.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if defect == "actor":
            request["actor_user_id"] = str(uuid4())
        else:
            request["preparation"]["completeness_locator"] = "Different synthetic verification"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        read_prepared_batch(path)


def test_batch_cli_does_not_leak_private_input_on_failure(tmp_path, monkeypatch, capsys) -> None:
    from legal_core import prepared_batch_operator

    path = _batch(tmp_path)

    async def fail(*args, **kwargs):
        raise ValueError("SYNTHETIC_PRIVATE_DOCUMENT_CONTENT")

    monkeypatch.setattr(prepared_batch_operator, "_run", fail)
    monkeypatch.setattr("sys.argv", ["prepared_batch_operator", str(path)])
    with pytest.raises(SystemExit) as error:
        prepared_batch_operator.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_DOCUMENT_CONTENT" not in output.out + output.err
    assert "no approval" in output.err


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
@pytest.mark.parametrize("reject_last", [False, True])
def test_batch_atomic_dry_run_commit_and_exact_replay(tmp_path: Path, reject_last: bool) -> None:
    async def scenario() -> None:
        path = _batch(tmp_path)
        batch = read_prepared_batch(path)
        first = batch.parts[0]
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                session.add_all([
                    LegalReviewMaterial(
                        id=first.request.material_id, package_key=f"batch-{uuid4().hex}",
                        original_filename="original.rtf", title=first.preparation.title,
                        kind="LEGAL_COPY", source_name="Synthetic source",
                        raw_mime_type="application/rtf", raw_bytes=_raw(first.preparation.title),
                        raw_size_bytes=len(first.raw_bytes), raw_sha256=first.request.raw_sha256,
                        received_at=datetime.now(UTC),
                    ),
                    User(id=first.request.actor_user_id,
                         telegram_user_id=uuid4().int % 10**12 + 1,
                         status="ACTIVE", system_role="LEGAL_EDITOR"),
                ])
            dry = await run_prepared_batch(factory, path)
            assert not dry.committed and len(dry.parts) == 2
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(
                    LegalMaterialPreparation
                ).where(LegalMaterialPreparation.material_id == first.request.material_id)) == 0
                assert await session.scalar(select(func.count()).select_from(LegalDocument).where(
                    LegalDocument.canonical_key.in_([p.corpus.document_key for p in batch.parts])
                )) == 0
            if reject_last:
                # Each manifest is internally consistent, but the second version's
                # parser must still match its preparation at the DB binding boundary.
                corpus_path = tmp_path / "manifest-part-2.json"
                corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
                corpus["fragments"][0]["text"] = "Synthetic text outside the verified original"
                from legal_core.corpus_loader import CorpusFragment, corpus_fragments_sha256

                corpus["fragments_sha256"] = corpus_fragments_sha256([
                    CorpusFragment.model_validate(item) for item in corpus["fragments"]
                ])
                corpus_path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
                with pytest.raises(ValueError):
                    await run_prepared_batch(factory, path, commit=True)
                async with factory() as session:
                    assert await session.scalar(select(func.count()).select_from(
                        LegalMaterialPreparation
                    ).where(LegalMaterialPreparation.material_id == first.request.material_id)) == 0
                    assert await session.scalar(select(func.count()).select_from(
                        LegalDocument
                    ).where(LegalDocument.canonical_key.in_(
                        [p.corpus.document_key for p in batch.parts]
                    ))) == 0
                return
            committed = await run_prepared_batch(factory, path, commit=True)
            replay = await run_prepared_batch(factory, path, commit=True)
            assert committed == replay
            assert committed.committed
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(
                    LegalMaterialPreparation
                ).where(LegalMaterialPreparation.material_id == first.request.material_id)) == 1
                assert await session.scalar(select(func.count()).select_from(
                    LegalPreparedPartVersion
                ).where(LegalPreparedPartVersion.material_id == first.request.material_id)) == 2
                ids = {item.version_id for item in committed.parts}
                preview = await group_preview(session, "general")
                assert ids <= {item.version_id for item in preview.ready}
                assert not any(item.title == first.preparation.title for item in preview.blocked)
                versions = list(await session.scalars(select(LegalVersion).where(
                    LegalVersion.id.in_(ids)
                )))
                assert all(item.approval_state == "REVIEW_REQUIRED" for item in versions)
                assert await session.scalar(select(func.count()).select_from(
                    LegalApprovalEvent
                ).where(LegalApprovalEvent.legal_version_id.in_(ids))) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
