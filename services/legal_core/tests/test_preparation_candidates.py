"""Private package enrichment uses synthetic public-document fixtures only."""

import hashlib
import json
import asyncio
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from legal_core.material_preparation import MaterialPreparationInput
from legal_core.preparation_candidates import enrich_preparation, generate_preparation_package
from legal_core.preparation_import import read_preparation_package


TITLE = 'Приказ Синтетического ведомства от 1 января 2001 г. N 7н "О примере"'
URL = "https://internet.garant.ru/document/redirect/11111111/0"
TEXT = TITLE + "\nСинтетический текст для проверки извлечения.\n"
RAW = (
    '{\\rtf1\\ansi\\ansicpg1251\\pard\\plain\\s1\\qc '
    '{\\field{\\*\\fldinst {HYPERLINK "' + URL + '"}}'
    '{\\fldrslt {\\cs24 ' + TITLE + '}}}\\par\\pard text}'
).encode("cp1251")


def preparation() -> MaterialPreparationInput:
    return MaterialPreparationInput.model_validate({
        "raw_sha256": hashlib.sha256(RAW).hexdigest(), "title": TITLE,
        "kind": "NORMATIVE", "group_key": "healthcare", "parser_version": "baseline-v1",
        "extraction_scope": "PARTIAL", "limitations": ["Tables need manual comparison"],
        "normalized_text": TEXT, "normalized_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
        "parts": [{"part_key": "document", "title": TITLE}],
    })


def test_enrichment_populates_evidence_but_preserves_unknown_edition_and_partial_text():
    previous = preparation()
    actual = enrich_preparation(RAW, previous)
    part = actual.parts[0]
    assert actual.raw_sha256 == previous.raw_sha256
    assert actual.normalized_sha256 == previous.normalized_sha256
    assert actual.normalized_text == previous.normalized_text
    assert actual.extraction_scope == "PARTIAL"
    assert actual.completeness_locator is None
    assert actual.source_url == URL
    assert part.part_key == "document"
    assert part.document_type == "Приказ"
    assert part.issuer == "Синтетического ведомства"
    assert part.official_number == "7н"
    assert part.adoption_date.isoformat() == "2001-01-01"
    assert all("candidate" in locator.lower() for locator in part.evidence.values())
    assert part.canonical_key is None
    assert part.publication_date is None
    assert part.version_date is None
    assert part.effective_from is None
    assert part.text_start is None
    assert part.text_sha256 is None
    assert "Tables need manual comparison" in actual.limitations
    assert actual.digest() != previous.digest()
    assert enrich_preparation(RAW, actual).digest() == actual.digest()


def test_enrichment_rejects_changed_original_and_conflicting_existing_identity():
    with pytest.raises(ValueError, match="checksum"):
        enrich_preparation(RAW + b" ", preparation())
    altered = preparation().model_dump()
    altered["parts"][0]["official_number"] = "999"
    with pytest.raises(ValueError, match="conflict"):
        enrich_preparation(RAW, MaterialPreparationInput.model_validate(altered))


def test_reference_preparation_is_preserved_exactly():
    raw = b"%PDF-1.7\nSynthetic reference fixture\n%%EOF"
    payload = preparation().model_dump() | {
        "raw_sha256": hashlib.sha256(raw).hexdigest(), "kind": "CLINICAL_REFERENCE",
        "group_key": "clinical", "parts": [],
    }
    reference = MaterialPreparationInput.model_validate(payload)
    assert enrich_preparation(raw, reference).digest() == reference.digest()


def test_generator_writes_importable_private_package_and_rejects_symlink_original(tmp_path: Path):
    inputs, originals = tmp_path / "baseline", tmp_path / "originals"
    inputs.mkdir()
    originals.mkdir()
    candidate = preparation()
    (inputs / "card.json").write_text(json.dumps(candidate.metadata(), ensure_ascii=False))
    (inputs / "text.txt").write_text(TEXT)
    (inputs / "package.json").write_text(json.dumps({
        "schema_version": 1, "package_key": "synthetic-package", "items": [{
            "original_filename": "example.rtf", "preparation_path": "card.json",
            "text_path": "text.txt",
        }],
    }))
    (originals / "example.rtf").write_bytes(RAW)
    output = tmp_path / "enriched"
    manifest = generate_preparation_package(
        inputs / "package.json", originals, output, expected_count=1,
    )
    package, actual = read_preparation_package(manifest)
    assert package.package_key == "synthetic-package"
    assert actual[0][0] == "example.rtf"
    assert actual[0][1].parts[0].official_number == "7н"
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in output.iterdir())
    with pytest.raises(ValueError, match="exists"):
        generate_preparation_package(inputs / "package.json", originals, output, expected_count=1)
    (originals / "example.rtf").unlink()
    (originals / "example.rtf").symlink_to(inputs / "text.txt")
    refused = tmp_path / "refused"
    with pytest.raises(ValueError, match="regular"):
        generate_preparation_package(inputs / "package.json", originals, refused, expected_count=1)
    assert not refused.exists()


def test_all_original_receipts_must_be_accounted_for_before_output_exists(tmp_path: Path):
    source = tmp_path / "baseline.json"
    source.write_text(json.dumps({
        "schema_version": 1, "package_key": "synthetic-package", "items": [],
    }))
    with pytest.raises(ValueError):
        generate_preparation_package(source, tmp_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_code_parts_keep_existing_keys_without_persisting_unverified_boundaries():
    title = "Налоговый кодекс Российской Федерации (НК РФ)"
    text = (
        title + "\nЧасть первая\nПринята Государственной Думой 1 января 2001 года\n"
        "Синтетическая первая часть.\n1 января 2001 г.\nN 1-ФЗ\n"
        "Часть вторая\nПринята Государственной Думой 2 января 2001 года\n"
        "Синтетическая вторая часть.\n2 января 2001 г.\nN 2-ФЗ\n"
    )
    raw = RAW.decode("cp1251").replace(TITLE, title).encode("cp1251")
    previous = MaterialPreparationInput.model_validate(preparation().model_dump() | {
        "raw_sha256": hashlib.sha256(raw).hexdigest(), "title": title,
        "normalized_text": text, "normalized_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "parts": [{"part_key": "part-1", "title": "First"},
                  {"part_key": "part-2", "title": "Second"}],
    })
    actual = enrich_preparation(raw, previous)
    assert [part.part_key for part in actual.parts] == ["part-1", "part-2"]
    assert [part.official_number for part in actual.parts] == ["1-ФЗ", "2-ФЗ"]
    assert all(part.issuer is None for part in actual.parts)
    assert all(part.text_start is None and part.text_sha256 is None for part in actual.parts)
    assert all(part.effective_from is None for part in actual.parts)


def test_cli_failure_does_not_expose_document_or_path(monkeypatch, capsys):
    from legal_core import preparation_candidates

    def rejected(*args, **kwargs):
        raise ValueError("SYNTHETIC_PRIVATE_DOCUMENT_CONTENT")

    monkeypatch.setattr(preparation_candidates, "generate_preparation_package", rejected)
    monkeypatch.setattr("sys.argv", ["preparation_candidates", "baseline", "originals", "output"])
    with pytest.raises(SystemExit) as failure:
        preparation_candidates.main()
    assert failure.value.code == 1
    captured = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_DOCUMENT_CONTENT" not in captured.out + captured.err
    assert "no database writes" in captured.err


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
def test_generated_import_appends_normative_revision_and_keeps_reference_without_approval(tmp_path):
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from legal_core.database import database_url
    from legal_core.material_preparation import store_preparation
    from legal_core.models import (
        LegalApprovalEvent, LegalMaterialPreparation, LegalReviewMaterial, LegalVersion,
    )
    from legal_core.preparation_import import ingest_preparation_package

    async def scenario():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        inputs, originals = tmp_path / "baseline", tmp_path / "originals"
        inputs.mkdir()
        originals.mkdir()
        key = "synthetic-candidates-" + uuid4().hex
        pdf = b"%PDF-1.7\nSynthetic reference fixture\n%%EOF"
        normative = preparation()
        reference = MaterialPreparationInput.model_validate(normative.model_dump() | {
            "raw_sha256": hashlib.sha256(pdf).hexdigest(), "kind": "CLINICAL_REFERENCE",
            "group_key": "clinical", "parts": [],
        })
        previous_ids = []
        materials = []
        items = []
        try:
            async with factory() as session, session.begin():
                versions_before = await session.scalar(
                    select(func.count()).select_from(LegalVersion)
                )
                approvals_before = await session.scalar(
                    select(func.count()).select_from(LegalApprovalEvent)
                )
                for index, (name, raw, payload) in enumerate((
                    ("synthetic.rtf", RAW, normative), ("reference.pdf", pdf, reference),
                )):
                    material = LegalReviewMaterial(
                        package_key=key, original_filename=name, title=payload.title,
                        kind="LEGAL_COPY" if payload.kind == "NORMATIVE" else "CLINICAL_REFERENCE",
                        source_name="Synthetic source", raw_bytes=raw,
                        raw_mime_type="application/rtf" if index == 0 else "application/pdf",
                        raw_sha256=payload.raw_sha256, raw_size_bytes=len(raw),
                        received_at=datetime.now(UTC),
                    )
                    session.add(material)
                    await session.flush()
                    materials.append(material.id)
                    previous_ids.append((await store_preparation(session, material.id, payload)).id)
                    (originals / name).write_bytes(raw)
                    (inputs / f"{index}.json").write_text(json.dumps(payload.metadata()))
                    (inputs / f"{index}.txt").write_text(payload.normalized_text)
                    items.append({"original_filename": name, "preparation_path": f"{index}.json",
                                  "text_path": f"{index}.txt"})
            (inputs / "package.json").write_text(json.dumps({
                "schema_version": 1, "package_key": key, "items": items,
            }))
            generated = generate_preparation_package(
                inputs / "package.json", originals, tmp_path / "enriched", expected_count=2,
            )
            result = await ingest_preparation_package(factory, generated)
            assert result[0] != previous_ids[0]
            assert result[1] == previous_ids[1]
            assert await ingest_preparation_package(factory, generated) == result
            async with factory() as session:
                revisions = (await session.execute(select(
                    LegalMaterialPreparation.revision, LegalMaterialPreparation.kind,
                ).where(LegalMaterialPreparation.material_id.in_(materials)))).all()
                assert sorted(revisions) == [
                    (1, "CLINICAL_REFERENCE"), (1, "NORMATIVE"), (2, "NORMATIVE"),
                ]
                assert await session.scalar(select(func.count()).select_from(
                    LegalVersion
                )) == versions_before
                assert await session.scalar(select(func.count()).select_from(
                    LegalApprovalEvent
                )) == approvals_before
        finally:
            await engine.dispose()

    asyncio.run(scenario())
