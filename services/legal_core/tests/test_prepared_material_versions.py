"""Synthetic contract and PostgreSQL checks for prepared-part/version bindings."""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.corpus_loader import CorpusFragment, corpus_fragments_sha256, ingest_manifest
from legal_core.database import database_url
from legal_core.legal_approval import ApprovalAttestation, approve_legal_version
from legal_core.material_preparation import MaterialPreparationInput
from legal_core.material_preparation import store_preparation
from legal_core.models import LegalPreparedPartVersion, LegalReviewMaterial, LegalVersion, User
from legal_core.normative_preparation import inspect_normative_rtf
from legal_core.normative_preparation import bind_prepared_part
from legal_core.normative_preparation import prepared_part_keys_match
from legal_core.runtime_db_role import provision_runtime_role


def _raw(title: str) -> bytes:
    url = "https://internet.garant.ru/document/redirect/11111111/0"
    heading = (
        '{\\field{\\*\\fldinst {HYPERLINK "' + url + '"}}'
        '{\\fldrslt {\\cs24 ' + title + '}}}'
    )
    return ("{\\rtf1\\ansi\\ansicpg1251\\pard\\plain\\s1\\qc "
            + heading + "\\par}").encode("cp1251")


def _prepared_input() -> MaterialPreparationInput:
    title = 'Федеральный закон от 1 января 2020 г. N 11-ФЗ "Синтетический"'
    normalized = title + "\nСтатья 1. Синтетическая норма для теста.\n"
    raw = _raw(title)
    candidate = inspect_normative_rtf(raw, normalized)
    part = candidate.parts[0]
    return MaterialPreparationInput.model_validate({
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "title": title,
        "kind": "NORMATIVE",
        "group_key": "general",
        "parser_version": "synthetic-rtf-v1",
        "source_url": "https://internet.garant.ru/document/redirect/11111111/0",
        "source_locator": candidate.source_locator,
        "parts": [{
            "part_key": "part-1",
            "title": title,
            "canonical_key": f"synthetic-11-fz-2020-{uuid4().hex}",
            "document_type": "Федеральный закон",
            "issuer": "Российская Федерация",
            "official_number": "11-ФЗ",
            "adoption_date": "2020-01-01",
            "publication_date": "2020-01-02",
            "version_date": "2026-10-01",
            "effective_from": "2020-01-10",
            "text_start": part.text_start,
            "text_end": part.text_end,
            "text_sha256": part.text_sha256,
            "evidence": {
                "title": "Synthetic heading line 1",
                "canonical_key": "Synthetic legal register row 1",
                "document_type": "Synthetic heading line 1",
                "issuer": "Synthetic official publication heading",
                "official_number": "Synthetic heading line 1",
                "adoption_date": "Synthetic heading line 1",
                "publication_date": "Synthetic publication line 2",
                "version_date": "Synthetic edition line 3",
                "effective_from": "Synthetic entry-into-force clause",
            },
        }],
        "extraction_scope": "FULL_DOCUMENT",
        "normalized_text": normalized,
        "normalized_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        "completeness_locator": "Synthetic complete article span",
    })


def test_scoped_normative_part_is_hash_bound_to_full_preparation() -> None:
    prepared = _prepared_input()
    part = prepared.parts[0]
    assert prepared.normalized_text[part.text_start:part.text_end] == prepared.normalized_text
    assert part.text_sha256 == hashlib.sha256(prepared.normalized_text.encode()).hexdigest()
    assert prepared.digest() != MaterialPreparationInput.model_validate(
        prepared.model_dump() | {"parts": [part.model_dump() | {"title": "Corrected title"}]}
    ).digest()


@pytest.mark.parametrize("change", [
    {"text_start": 1},
    {"text_end": 999_999},
    {"text_sha256": "a" * 64},
    {"text_sha256": None},
])
def test_malformed_part_scope_cannot_be_stored(change: dict) -> None:
    prepared = _prepared_input()
    part = prepared.parts[0]
    with pytest.raises(ValidationError):
        MaterialPreparationInput.model_validate(prepared.model_dump() | {
            "parts": [part.model_dump() | change],
        })


def test_whitespace_only_completeness_locator_is_not_evidence() -> None:
    prepared = _prepared_input()
    with pytest.raises(ValidationError, match="completeness"):
        MaterialPreparationInput.model_validate(prepared.model_dump() | {
            "completeness_locator": " \t\n",
        })


def test_single_part_alias_matches_only_the_entire_exact_original_text() -> None:
    prepared = _prepared_input()
    legacy = prepared.model_copy(update={
        "parts": [prepared.parts[0].model_copy(update={"part_key": "document"})],
    })
    candidate = inspect_normative_rtf(_raw(prepared.title), prepared.normalized_text)
    assert prepared_part_keys_match(legacy, candidate)
    for change in (
        {"part_key": "other"}, {"text_start": 1},
        {"text_end": len(prepared.normalized_text) - 1}, {"text_sha256": "a" * 64},
    ):
        altered = legacy.model_copy(update={
            "parts": [legacy.parts[0].model_copy(update=change)],
        })
        assert not prepared_part_keys_match(altered, candidate)
    assert not prepared_part_keys_match(legacy.model_copy(update={
        "raw_sha256": "a" * 64,
    }), candidate)
    assert not prepared_part_keys_match(legacy.model_copy(update={
        "extraction_scope": "PARTIAL",
    }), candidate)


def _bundle_input() -> MaterialPreparationInput:
    run_key = uuid4().hex
    title = "Налоговый кодекс Российской Федерации (НК РФ)"
    normalized = (
        title + "\nЧасть первая\nПринята Государственной Думой 1 января 2000 года\n"
        "Статья 1. Синтетическая первая норма для теста.\n"
        "1 января 2000 г.\nN 1-ФЗ\n"
        "Часть вторая\nПринята Государственной Думой 2 января 2000 года\n"
        "Статья 2. Синтетическая вторая норма для теста.\n"
        "2 января 2000 г.\nN 2-ФЗ\n"
    )
    raw = _raw(title)
    candidate = inspect_normative_rtf(raw, normalized)
    parts = []
    for index, parsed in enumerate(candidate.parts, start=1):
        parts.append({
            "part_key": parsed.part_key,
            "title": f"Налоговый кодекс Российской Федерации — часть {index}",
            "canonical_key": f"synthetic-tax-code-part-{index}-{run_key}",
            "document_type": "Кодекс",
            "issuer": "Российская Федерация",
            "official_number": f"{index}-ФЗ",
            "adoption_date": f"2000-01-0{index}",
            "publication_date": f"2000-01-0{index}",
            "version_date": "2026-10-01",
            "effective_from": f"2000-01-1{index}",
            "text_start": parsed.text_start,
            "text_end": parsed.text_end,
            "text_sha256": parsed.text_sha256,
            "evidence": {
                "title": f"Synthetic official code part {index}",
                "canonical_key": f"Synthetic register part {index}",
                "document_type": f"Synthetic official code part {index}",
                "issuer": f"Synthetic official code part {index}",
                "official_number": f"Synthetic signature part {index}",
                "adoption_date": f"Synthetic signature part {index}",
                "publication_date": f"Synthetic publication part {index}",
                "version_date": f"Synthetic edition part {index}",
                "effective_from": f"Synthetic effective clause part {index}",
            },
        })
    return MaterialPreparationInput.model_validate({
        "raw_sha256": hashlib.sha256(raw).hexdigest(), "title": title,
        "kind": "NORMATIVE", "group_key": "general",
        "parser_version": "synthetic-rtf-v1", "source_url": candidate.source_url,
        "source_locator": candidate.source_locator, "parts": parts,
        "extraction_scope": "FULL_DOCUMENT", "normalized_text": normalized,
        "normalized_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        "completeness_locator": "Synthetic complete two-part text",
    })


def test_multipart_candidate_keys_are_never_aliased() -> None:
    prepared = _bundle_input()
    candidate = inspect_normative_rtf(_raw(prepared.title), prepared.normalized_text)
    assert prepared_part_keys_match(prepared, candidate)
    for keys in (("document", "part-2"), ("part-2", "part-1")):
        altered = prepared.model_copy(update={
            "parts": [part.model_copy(update={"part_key": key})
                      for part, key in zip(prepared.parts, keys, strict=True)],
        })
        assert not prepared_part_keys_match(altered, candidate)


def _write_manifest(
    tmp_path: Path, prepared: MaterialPreparationInput, part_index: int = 0,
) -> Path:
    artifact = tmp_path / "original.rtf"
    artifact.write_bytes(_raw(prepared.title))
    part = prepared.parts[part_index]
    scoped = prepared.normalized_text[part.text_start:part.text_end]
    fragment_text = next(line for line in scoped.splitlines() if line.startswith("Статья"))
    fragment = CorpusFragment(
        ordinal=1, article=str(part_index + 1), part=None, point=None, heading=None,
        structural_path=f"Статья {part_index + 1}", text=fragment_text,
    )
    payload = {
        "manifest_version": "dental-legal-corpus.v4",
        "source_key": "garant", "source_revision": 1, "source_name": "Гарант",
        "source_trust_level": "VERIFIED_COPY",
        "source_base_url": "https://internet.garant.ru/",
        "source_url": prepared.source_url,
        "source_external_id": "11111111",
        "allowed_hosts": ["internet.garant.ru"],
        "document_key": part.canonical_key,
        "document_type": part.document_type,
        "title": part.title,
        "issuer": part.issuer,
        "official_number": part.official_number,
        "adoption_date": part.adoption_date.isoformat(),
        "publication_date": part.publication_date.isoformat(),
        "version_date": part.version_date.isoformat(),
        "effective_from": part.effective_from.isoformat(),
        "effective_to": part.effective_to.isoformat() if part.effective_to else None,
        "approval_state": "REVIEW_REQUIRED",
        "artifact_kind": "THIRD_PARTY_VERIFIED_COPY",
        "artifact_mime_type": "application/rtf",
        "artifact_sha256": prepared.raw_sha256,
        "artifact_path": artifact.name,
        "artifact_retrieved_at": "2026-10-01T12:00:00Z",
        "artifact_size_bytes": artifact.stat().st_size,
        "normalized_text": scoped,
        "normalized_sha256": part.text_sha256,
        "fragments_sha256": corpus_fragments_sha256([fragment]),
        "normalization_scope": "FULL_DOCUMENT",
        "parser_version": prepared.parser_version,
        "fragments": [fragment.model_dump(mode="json")],
    }
    path = tmp_path / f"manifest-part-{part_index + 1}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


async def _insert_unvalidated_preparation(
    session, material_id, prepared: MaterialPreparationInput, metadata: dict,
):
    """Simulate a compromised runtime writer bypassing the Python contract."""
    return (await session.execute(text("""
        INSERT INTO legal_material_preparations
          (material_id, raw_sha256, revision, preparation_sha256, title, kind,
           group_key, metadata_json, normalized_text)
        VALUES
          (:material_id, :raw_sha256, 1,
           legal_regression_result_sha256(CAST(:metadata AS jsonb)),
           :title, 'NORMATIVE', :group_key, CAST(:metadata AS jsonb), :normalized_text)
        RETURNING id
    """), {
        "material_id": material_id, "raw_sha256": prepared.raw_sha256,
        "metadata": json.dumps(metadata, ensure_ascii=False),
        "title": prepared.title, "group_key": prepared.group_key,
        "normalized_text": prepared.normalized_text,
    })).scalar_one()


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
@pytest.mark.parametrize("defect", [
    "other_single_key", "document_in_bundle", "reordered_bundle_keys",
])
def test_binding_rejects_false_aliases_without_inserting_associations(
    tmp_path: Path, defect: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        prepared = _prepared_input() if defect == "other_single_key" else _bundle_input()
        payload = prepared.model_dump()
        if defect == "other_single_key":
            payload["parts"][0]["part_key"] = "other"
        elif defect == "document_in_bundle":
            payload["parts"][0]["part_key"] = "document"
        else:
            payload["parts"][0]["part_key"] = "part-2"
            payload["parts"][1]["part_key"] = "part-1"
        prepared = MaterialPreparationInput.model_validate(payload)
        try:
            version_id = await ingest_manifest(factory, _write_manifest(tmp_path, prepared))
            async with factory() as session, session.begin():
                raw = _raw(prepared.title)
                material = LegalReviewMaterial(
                    package_key=f"synthetic-false-alias-{uuid4().hex}",
                    original_filename="original.rtf", title=prepared.title,
                    kind="LEGAL_COPY", source_name="Synthetic source",
                    raw_mime_type="application/rtf", raw_bytes=raw,
                    raw_size_bytes=len(raw), raw_sha256=prepared.raw_sha256,
                    received_at=datetime.now(UTC),
                )
                actor = User(telegram_user_id=uuid4().int % 10**12 + 1,
                             status="ACTIVE", system_role="LEGAL_EDITOR")
                session.add_all([material, actor])
                await session.flush()
                preparation = await store_preparation(session, material.id, prepared)
                with pytest.raises(ValueError, match="boundaries"):
                    await bind_prepared_part(
                        session, preparation_id=preparation.id,
                        part_key=prepared.parts[0].part_key,
                        legal_version_id=version_id, actor_user_id=actor.id,
                    )
                assert await session.scalar(select(func.count()).select_from(
                    LegalPreparedPartVersion
                ).where(LegalPreparedPartVersion.material_id == material.id)) == 0
                version = await session.get(LegalVersion, version_id)
                assert version.approval_state == "REVIEW_REQUIRED"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
@pytest.mark.parametrize("defect", [
    "overlap_in_other_part", "gap_in_other_part", "bad_other_part_hash",
    "empty_completeness", "limitations_on_full_document",
])
def test_direct_sql_cannot_bind_incomplete_preparation(tmp_path: Path, defect: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        prepared = _bundle_input()
        metadata = prepared.metadata()
        if defect == "overlap_in_other_part":
            metadata["parts"][1]["text_start"] = 0
        elif defect == "gap_in_other_part":
            metadata["parts"][1]["text_start"] += 1
        elif defect == "bad_other_part_hash":
            metadata["parts"][1]["text_sha256"] = "a" * 64
        elif defect == "empty_completeness":
            metadata["completeness_locator"] = " \t\n"
        else:
            metadata["limitations"] = ["Synthetic omitted section"]
        try:
            version_id = await ingest_manifest(factory, _write_manifest(tmp_path, prepared))
            async with factory() as session, session.begin():
                raw = _raw(prepared.title)
                material = LegalReviewMaterial(
                    package_key=f"test-p9-forged-{uuid4().hex}",
                    original_filename="bundle.rtf", title=prepared.title,
                    kind="LEGAL_COPY", source_name="Synthetic source",
                    raw_mime_type="application/rtf", raw_bytes=raw,
                    raw_size_bytes=len(raw), raw_sha256=prepared.raw_sha256,
                    received_at=datetime.now(UTC),
                )
                actor = User(telegram_user_id=uuid4().int % 10**12 + 1,
                             status="ACTIVE", system_role="LEGAL_EDITOR")
                session.add_all([material, actor])
                await session.flush()
                preparation_id = await _insert_unvalidated_preparation(
                    session, material.id, prepared, metadata,
                )
                material_id, actor_id = material.id, actor.id
            async with factory() as session, session.begin():
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        session.add(LegalPreparedPartVersion(
                            material_id=material_id, preparation_id=preparation_id,
                            raw_sha256=prepared.raw_sha256, part_key="part-1",
                            part_text_sha256=prepared.parts[0].text_sha256,
                            legal_version_id=version_id, created_by_user_id=actor_id,
                        ))
                        await session.flush()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
def test_direct_sql_requires_effective_to_provenance(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        basic = _prepared_input().model_dump(mode="json")
        basic["parts"][0]["effective_to"] = "2030-01-01"
        basic["parts"][0]["evidence"]["effective_to"] = "Synthetic sunset clause"
        prepared = MaterialPreparationInput.model_validate(basic)
        metadata = prepared.metadata()
        del metadata["parts"][0]["evidence"]["effective_to"]
        try:
            version_id = await ingest_manifest(factory, _write_manifest(tmp_path, prepared))
            async with factory() as session, session.begin():
                raw = _raw(prepared.title)
                material = LegalReviewMaterial(
                    package_key=f"test-p9-sunset-{uuid4().hex}",
                    original_filename="sunset.rtf", title=prepared.title,
                    kind="LEGAL_COPY", source_name="Synthetic source",
                    raw_mime_type="application/rtf", raw_bytes=raw,
                    raw_size_bytes=len(raw), raw_sha256=prepared.raw_sha256,
                    received_at=datetime.now(UTC),
                )
                actor = User(telegram_user_id=uuid4().int % 10**12 + 1,
                             status="ACTIVE", system_role="LEGAL_EDITOR")
                session.add_all([material, actor])
                await session.flush()
                preparation_id = await _insert_unvalidated_preparation(
                    session, material.id, prepared, metadata,
                )
                material_id, actor_id = material.id, actor.id
            async with factory() as session, session.begin():
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        session.add(LegalPreparedPartVersion(
                            material_id=material_id, preparation_id=preparation_id,
                            raw_sha256=prepared.raw_sha256, part_key="part-1",
                            part_text_sha256=prepared.parts[0].text_sha256,
                            legal_version_id=version_id, created_by_user_id=actor_id,
                        ))
                        await session.flush()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
def test_direct_sql_cannot_bind_an_already_approved_version(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        prepared = _prepared_input()
        try:
            version_id = await ingest_manifest(factory, _write_manifest(tmp_path, prepared))
            reviewer_telegram_id = uuid4().int % 10**12 + 1
            async with factory() as session, session.begin():
                raw = _raw(prepared.title)
                material = LegalReviewMaterial(
                    package_key=f"test-p9-boundary-{uuid4().hex}",
                    original_filename="original.rtf", title=prepared.title,
                    kind="LEGAL_COPY",
                    source_name="Synthetic source", raw_mime_type="application/rtf",
                    raw_bytes=raw, raw_size_bytes=len(raw),
                    raw_sha256=prepared.raw_sha256, received_at=datetime.now(UTC),
                )
                actor = User(telegram_user_id=reviewer_telegram_id, status="ACTIVE",
                             system_role="LEGAL_EDITOR")
                session.add_all([material, actor])
                await session.flush()
                preparation = await store_preparation(session, material.id, prepared)
                material_id, preparation_id, actor_id = material.id, preparation.id, actor.id
            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                attestation = ApprovalAttestation(
                    reviewer_telegram_user_id=reviewer_telegram_id,
                    version_id=version_id,
                    expected_sha256=version.raw_sha256,
                    expected_normalized_sha256=version.normalized_sha256,
                    expected_fragments_sha256=version.fragments_sha256,
                    expected_effective_from=version.effective_from,
                    expected_effective_to=version.effective_to,
                    source_is_official=False, official_text_compared=True,
                    artifact_is_complete=True, effective_dates_verified=True,
                    fragments_verified=True,
                )
            assert await approve_legal_version(factory, attestation) == version_id
            async with factory() as session, session.begin():
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        session.add(LegalPreparedPartVersion(
                            material_id=material_id, preparation_id=preparation_id,
                            raw_sha256=prepared.raw_sha256, part_key="part-1",
                            part_text_sha256=prepared.parts[0].text_sha256,
                            legal_version_id=version_id, created_by_user_id=actor_id,
                        ))
                        await session.flush()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
def test_binding_is_exact_immutable_idempotent_and_never_approves(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        prepared = _prepared_input()
        manifest = _write_manifest(tmp_path, prepared)
        try:
            version_id = await ingest_manifest(factory, manifest)
            assert await ingest_manifest(factory, manifest) == version_id
            async with factory() as session, session.begin():
                raw = _raw(prepared.title)
                material = LegalReviewMaterial(
                    package_key=f"test-p9-{uuid4().hex}", original_filename="original.rtf",
                    title=prepared.title, kind="LEGAL_COPY", source_name="Synthetic source",
                    raw_mime_type="application/rtf", raw_bytes=raw,
                    raw_size_bytes=len(raw), raw_sha256=prepared.raw_sha256,
                    received_at=datetime.now(UTC),
                )
                actor = User(telegram_user_id=uuid4().int % 10**12 + 1,
                             status="ACTIVE", system_role="LEGAL_EDITOR")
                session.add_all([material, actor])
                await session.flush()
                preparation = await store_preparation(session, material.id, prepared)
                material_id, preparation_id, actor_id = material.id, preparation.id, actor.id
            async with factory() as session, session.begin():
                with pytest.raises(ValueError, match="part key"):
                    await bind_prepared_part(
                        session, preparation_id=preparation_id, part_key="../invalid",
                        legal_version_id=version_id, actor_user_id=actor_id,
                    )
                binding = await bind_prepared_part(
                    session, preparation_id=preparation_id, part_key="part-1",
                    legal_version_id=version_id, actor_user_id=actor_id,
                )
                binding_id = binding.id
            async with factory() as session, session.begin():
                replay = await bind_prepared_part(
                    session, preparation_id=preparation_id, part_key="part-1",
                    legal_version_id=version_id, actor_user_id=actor_id,
                )
                assert replay.id == binding_id
                assert await session.scalar(select(func.count()).select_from(
                    LegalPreparedPartVersion
                ).where(LegalPreparedPartVersion.material_id == material_id)) == 1
                version = await session.get(LegalVersion, version_id)
                assert version.approval_state == "REVIEW_REQUIRED"
                assert await session.scalar(text(
                    "SELECT count(*) FROM production_legal_fragments WHERE version_id=:id"
                ), {"id": version_id}) == 0
                for statement in (
                    "UPDATE legal_review_materials SET title='Forged receipt' WHERE id=:id",
                    "UPDATE legal_review_materials SET kind='CLINICAL_REFERENCE' WHERE id=:id",
                ):
                    with pytest.raises(DBAPIError):
                        async with session.begin_nested():
                            await session.execute(text(statement), {"id": material_id})
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        await session.execute(text(
                            "UPDATE legal_prepared_part_versions SET part_key='forged' WHERE id=:id"
                        ), {"id": binding_id})
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        await session.execute(text(
                            "DELETE FROM legal_prepared_part_versions WHERE id=:id"
                        ), {"id": binding_id})
                actor = await session.get(User, actor_id)
                actor.status = "REVOKED"
                await session.flush()
                with pytest.raises(PermissionError, match="LEGAL_EDITOR"):
                    await bind_prepared_part(
                        session, preparation_id=preparation_id, part_key="part-1",
                        legal_version_id=version_id, actor_user_id=actor_id,
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
def test_bundle_parts_bind_separately_and_failed_association_rolls_back(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        prepared = _bundle_input()
        try:
            first_id = await ingest_manifest(factory, _write_manifest(tmp_path, prepared, 0))
            second_id = await ingest_manifest(factory, _write_manifest(tmp_path, prepared, 1))
            assert first_id != second_id
            async with factory() as session, session.begin():
                raw = _raw(prepared.title)
                material = LegalReviewMaterial(
                    package_key=f"test-p9-bundle-{uuid4().hex}",
                    original_filename="bundle.rtf", title=prepared.title,
                    kind="LEGAL_COPY", source_name="Synthetic source",
                    raw_mime_type="application/rtf", raw_bytes=raw,
                    raw_size_bytes=len(raw), raw_sha256=prepared.raw_sha256,
                    received_at=datetime.now(UTC),
                )
                actor = User(telegram_user_id=uuid4().int % 10**12 + 1,
                             status="ACTIVE", system_role="LEGAL_EDITOR")
                session.add_all([material, actor])
                await session.flush()
                preparation = await store_preparation(session, material.id, prepared)
                preparation_id, actor_id = preparation.id, actor.id
            async with factory() as session, session.begin():
                await bind_prepared_part(
                    session, preparation_id=preparation_id, part_key="part-1",
                    legal_version_id=first_id, actor_user_id=actor_id,
                )
            async with factory() as session, session.begin():
                with pytest.raises(ValueError, match=r"identity|text|part"):
                    await bind_prepared_part(
                        session, preparation_id=preparation_id, part_key="part-2",
                        legal_version_id=first_id, actor_user_id=actor_id,
                    )
                assert await session.scalar(select(func.count()).select_from(
                    LegalPreparedPartVersion
                ).where(LegalPreparedPartVersion.preparation_id == preparation_id)) == 1
            with pytest.raises(RuntimeError, match="synthetic rollback"):
                async with factory() as session, session.begin():
                    await bind_prepared_part(
                        session, preparation_id=preparation_id, part_key="part-2",
                        legal_version_id=second_id, actor_user_id=actor_id,
                    )
                    raise RuntimeError("synthetic rollback")
            async with factory() as session, session.begin():
                assert await session.scalar(select(func.count()).select_from(
                    LegalPreparedPartVersion
                ).where(LegalPreparedPartVersion.preparation_id == preparation_id)) == 1
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        session.add(LegalPreparedPartVersion(
                            material_id=material.id, preparation_id=preparation_id,
                            raw_sha256=prepared.raw_sha256, part_key="part-2",
                            part_text_sha256=prepared.parts[1].text_sha256,
                            legal_version_id=first_id, created_by_user_id=actor_id,
                        ))
                        await session.flush()
                await bind_prepared_part(
                    session, preparation_id=preparation_id, part_key="part-2",
                    legal_version_id=second_id, actor_user_id=actor_id,
                )
                assert await session.scalar(select(func.count()).select_from(
                    LegalPreparedPartVersion
                ).where(LegalPreparedPartVersion.preparation_id == preparation_id)) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1" or not os.getenv("POSTGRES_APP_USER"),
    reason="disposable PostgreSQL runtime role required",
)
def test_runtime_role_can_append_but_cannot_rewrite_association_history() -> None:
    provision_runtime_role()

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        try:
            async with engine.connect() as connection:
                access = (await connection.execute(text(
                    "SELECT "
                    "has_table_privilege(current_user, 'public.legal_prepared_part_versions', "
                    "'SELECT') AS can_select, "
                    "has_table_privilege(current_user, 'public.legal_prepared_part_versions', "
                    "'INSERT') AS can_insert, "
                    "has_table_privilege(current_user, 'public.legal_prepared_part_versions', "
                    "'UPDATE') AS can_update, "
                    "has_table_privilege(current_user, 'public.legal_prepared_part_versions', "
                    "'DELETE') AS can_delete, "
                    "has_function_privilege('public', "
                    "'public.guard_prepared_part_version()', 'EXECUTE') "
                    "AS public_can_execute_guard"
                ))).mappings().one()
                assert access == {
                    "can_select": True, "can_insert": True,
                    "can_update": False, "can_delete": False,
                    "public_can_execute_guard": False,
                }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL")
def test_superseded_preparation_cannot_create_a_new_binding(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        prepared = _prepared_input()
        try:
            version_id = await ingest_manifest(factory, _write_manifest(tmp_path, prepared))
            async with factory() as session, session.begin():
                raw = _raw(prepared.title)
                material = LegalReviewMaterial(
                    package_key=f"test-p9-stale-{uuid4().hex}",
                    original_filename="stale.rtf", title=prepared.title,
                    kind="LEGAL_COPY", source_name="Synthetic source",
                    raw_mime_type="application/rtf", raw_bytes=raw,
                    raw_size_bytes=len(raw), raw_sha256=prepared.raw_sha256,
                    received_at=datetime.now(UTC),
                )
                actor = User(telegram_user_id=uuid4().int % 10**12 + 1,
                             status="ACTIVE", system_role="LEGAL_EDITOR")
                session.add_all([material, actor])
                await session.flush()
                old = await store_preparation(session, material.id, prepared)
                revised = MaterialPreparationInput.model_validate(prepared.model_dump() | {
                    "parser_version": "synthetic-rtf-v2",
                })
                current = await store_preparation(session, material.id, revised)
                assert current.revision == old.revision + 1
                old_id, actor_id = old.id, actor.id
            async with factory() as session, session.begin():
                with pytest.raises(ValueError, match="newer revision"):
                    await bind_prepared_part(
                        session, preparation_id=old_id, part_key="part-1",
                        legal_version_id=version_id, actor_user_id=actor_id,
                    )
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        session.add(LegalPreparedPartVersion(
                            material_id=material.id, preparation_id=old_id,
                            raw_sha256=prepared.raw_sha256, part_key="part-1",
                            part_text_sha256=prepared.parts[0].text_sha256,
                            legal_version_id=version_id, created_by_user_id=actor_id,
                        ))
                        await session.flush()
                assert await session.scalar(select(func.count()).select_from(
                    LegalPreparedPartVersion
                ).where(LegalPreparedPartVersion.preparation_id == old_id)) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
