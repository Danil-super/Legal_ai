import hashlib
import json
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from legal_core.preparation_import import read_preparation_package


def test_cli_failure_does_not_print_document_content(monkeypatch, capsys):
    from legal_core import preparation_import

    async def rejected(*args):
        raise ValueError("SYNTHETIC_PRIVATE_DOCUMENT_CONTENT")

    monkeypatch.setattr(preparation_import, "_run", rejected)
    monkeypatch.setattr("sys.argv", ["preparation_import", "package.json"])
    with pytest.raises(SystemExit) as failure:
        preparation_import.main()
    assert failure.value.code == 1
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_DOCUMENT_CONTENT" not in output.out + output.err
    assert "no legal approval" in output.err


def package(directory: Path) -> Path:
    source = directory / "package.json"
    text = "Synthetic text for a public reference fixture."
    (directory / "text.txt").write_text(text)
    (directory / "card.json").write_text(json.dumps({
        "raw_sha256": "a" * 64, "title": "Synthetic reference",
        "kind": "CLINICAL_REFERENCE", "group_key": "clinical", "parser_version": "test-v1",
        "extraction_scope": "PARTIAL", "limitations": ["Synthetic cover has no text layer"],
        "normalized_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }))
    source.write_text(json.dumps({"schema_version": 1, "package_key": "test-package", "items": [{
        "original_filename": "reference.pdf", "preparation_path": "card.json",
        "text_path": "text.txt",
    }]}))
    return source


def test_import_input_binds_text_and_preserves_unknown_dates(tmp_path: Path) -> None:
    manifest, items = read_preparation_package(package(tmp_path))
    assert manifest.package_key == "test-package"
    assert len(items) == 1
    assert items[0][0] == "reference.pdf"
    assert items[0][1].reference_year is None
    assert items[0][1].normalized_text.startswith("Synthetic text")


@pytest.mark.parametrize("field,value", [
    ("preparation_path", "../outside.json"), ("text_path", "/etc/passwd"),
    ("original_filename", "../other.pdf"),
])
def test_import_rejects_paths_outside_its_package(tmp_path: Path, field: str, value: str) -> None:
    source = package(tmp_path)
    value_json = json.loads(source.read_text())
    value_json["items"][0][field] = value
    source.write_text(json.dumps(value_json))
    with pytest.raises(ValueError):
        read_preparation_package(source)


def test_import_rejects_symlinks_and_checksum_mismatch(tmp_path: Path) -> None:
    source = package(tmp_path)
    actual = tmp_path / "other.txt"
    (tmp_path / "text.txt").rename(actual)
    (tmp_path / "text.txt").symlink_to(actual)
    with pytest.raises(ValueError, match="regular"):
        read_preparation_package(source)
    (tmp_path / "text.txt").unlink()
    (tmp_path / "text.txt").write_text("Tampered content")
    with pytest.raises(ValueError):
        read_preparation_package(source)


def test_import_rejects_duplicate_originals_and_unknown_fields(tmp_path: Path) -> None:
    source = package(tmp_path)
    payload = json.loads(source.read_text())
    payload["items"] *= 2
    source.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate"):
        read_preparation_package(source)


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
def test_import_is_atomic_and_idempotent(tmp_path: Path) -> None:
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from legal_core.database import database_url
    from legal_core.models import LegalMaterialPreparation, LegalReviewMaterial
    from legal_core.preparation_import import ingest_preparation_package

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        key = f"prep-import-{uuid4().hex}"
        ids = []
        entries = []
        try:
            async with factory() as session, session.begin():
                for index in range(2):
                    raw = f"%PDF-1.7\nSynthetic reference {index}\n%%EOF".encode()
                    digest = hashlib.sha256(raw).hexdigest()
                    material = LegalReviewMaterial(
                        package_key=key, original_filename=f"{index}.pdf", title="Original",
                        kind="CLINICAL_REFERENCE", source_name="Synthetic source",
                        raw_mime_type="application/pdf", raw_bytes=raw,
                        raw_sha256=digest, raw_size_bytes=len(raw), received_at=datetime.now(UTC),
                    )
                    session.add(material)
                    await session.flush()
                    ids.append(material.id)
                    (tmp_path / f"{index}.json").write_text(json.dumps({
                        "raw_sha256": digest, "title": "Synthetic reference",
                        "kind": "CLINICAL_REFERENCE", "group_key": "clinical",
                        "parser_version": "test-v1", "extraction_scope": "NONE",
                    }))
                    entries.append({"original_filename": f"{index}.pdf",
                                    "preparation_path": f"{index}.json"})
            manifest = tmp_path / "import.json"
            manifest.write_text(json.dumps({"schema_version": 1, "package_key": key,
                                            "items": entries}))
            card = tmp_path / "1.json"
            correct = card.read_text()
            card.write_text(json.dumps(json.loads(correct) | {"raw_sha256": "b" * 64}))
            with pytest.raises(ValueError, match="original"):
                await ingest_preparation_package(factory, manifest)
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(
                    LegalMaterialPreparation
                ).where(LegalMaterialPreparation.material_id.in_(ids))) == 0
            card.write_text(correct)
            first = await ingest_preparation_package(factory, manifest)
            assert await ingest_preparation_package(factory, manifest) == first
            assert len(first) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())
