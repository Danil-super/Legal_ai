"""Synthetic, text-free package evidence matrix contract and PostgreSQL checks."""

import asyncio
import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.database import database_url
from legal_core.material_preparation import MaterialPreparationInput, store_preparation
from legal_core.models import (
    LegalDocument,
    LegalMaterialPreparation,
    LegalReviewMaterial,
    LegalSource,
    LegalVersion,
)
from legal_core.package_evidence_matrix import (
    FieldEvidence,
    PackageEvidenceRequest,
    PackageEvidenceMatrix,
    PartEvidence,
    _part_evidence,
    _write_private_matrix,
    generate_evidence_matrix,
    load_private_request,
    validate_evidence_matrix,
)


def _inventory() -> dict:
    groups = (
        ["clinical"] * 7
        + ["labour"] * 7
        + ["courts"] * 10
        + ["privacy"] * 4
        + ["licensing"] * 3
        + ["healthcare"] * 22
        + ["general"] * 5
    )
    originals = []
    for index, group in enumerate(groups):
        kind = (
            "CLINICAL_REFERENCE"
            if group == "clinical"
            else "REFERENCE_FORM"
            if group == "healthcare" and index == 52
            else "NORMATIVE"
        )
        part_count = 4 if index == 53 else 2 if index == 54 else 1
        originals.append(
            {
                "material_id": str(uuid4()),
                "raw_sha256": f"{index + 1:064x}",
                "kind": kind,
                "group_key": group,
                "expected_part_keys": (
                    [f"part-{n}" for n in range(1, part_count + 1)] if kind == "NORMATIVE" else []
                ),
            }
        )
    return {
        "schema_version": "package-evidence.v1",
        "package_key": "synthetic-package",
        "originals": originals,
        "editor_visible_version_ids": [str(uuid4()) for _ in range(6)],
    }


def _synthetic_corpus_version(
    document_id, source_id, *, version_no: int, effective_to: date | None = None
) -> LegalVersion:
    version_id = uuid4()
    raw = f"synthetic corpus bytes {version_id}".encode()
    normalized = f"synthetic corpus text {version_id}"
    return LegalVersion(
        id=version_id,
        document_id=document_id,
        source_id=source_id,
        version_no=version_no,
        source_external_id=str(version_id),
        source_url=f"https://example.invalid/{version_id}",
        effective_from=date(2020, 1, 1),
        effective_to=effective_to,
        approval_state="REVIEW_REQUIRED",
        artifact_kind="NORMALIZED_EXCERPT",
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        raw_mime_type="application/rtf",
        raw_bytes=raw,
        raw_size_bytes=len(raw),
        normalized_text=normalized,
        normalized_sha256=hashlib.sha256(normalized.encode()).hexdigest(),
        fragments_sha256="b" * 64,
        normalization_scope="SELECTED_EXCERPT",
        parser_version="synthetic",
        regression_passed=False,
    )


def test_inventory_requires_exact_package_shape_without_document_text() -> None:
    request = PackageEvidenceRequest.model_validate(_inventory())
    assert len(request.originals) == 58
    assert sum(len(item.expected_part_keys) for item in request.originals) == 54
    assert sum(item.kind != "NORMATIVE" for item in request.originals) == 8
    assert "normalized_text" not in request.model_dump_json()


def test_inventory_rejects_ambiguous_legacy_version_ids() -> None:
    payload = _inventory()
    payload["legacy_version_ids"] = payload.pop("editor_visible_version_ids")
    with pytest.raises(ValidationError):
        PackageEvidenceRequest.model_validate(payload)


@pytest.mark.parametrize(
    "change", ["duplicate_id", "missing_reference", "wrong_parts", "unknown_field", "wrong_group"]
)
def test_inventory_fails_closed_on_shape_or_extra_content(change: str) -> None:
    payload = _inventory()
    if change == "duplicate_id":
        payload["originals"][1]["material_id"] = payload["originals"][0]["material_id"]
    elif change == "missing_reference":
        payload["originals"].pop(0)
    elif change == "wrong_parts":
        payload["originals"][-1]["expected_part_keys"] = ["part-1", "part-3"]
    elif change == "unknown_field":
        payload["originals"][0]["normalized_text"] = "sensitive synthetic text"
    else:
        payload["originals"][0]["group_key"] = "general"
    with pytest.raises(ValidationError):
        PackageEvidenceRequest.model_validate(payload)


def test_private_inventory_rejects_unsafe_permissions_and_symlink(tmp_path: Path) -> None:
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(_inventory()), encoding="utf-8")
    with pytest.raises(ValueError, match="private regular file"):
        load_private_request(path)
    path.chmod(0o600)
    assert len(load_private_request(path).originals) == 58
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises((OSError, ValueError)):
        load_private_request(link)
    payload = _inventory()
    payload["originals"][0]["normalized_text"] = "SENSITIVE_PATIENT_MARKER"
    path.write_text(json.dumps(payload), encoding="utf-8")
    failed = subprocess.run(
        [
            sys.executable,
            "-m",
            "legal_core.package_evidence_matrix",
            "validate",
            str(path),
            str(tmp_path / "absent.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 1
    assert "SENSITIVE_PATIENT_MARKER" not in failed.stdout + failed.stderr
    assert failed.stderr.strip() == (
        "package evidence operation failed; no database writes performed"
    )


def test_matrix_rejects_inconsistent_candidate_status_and_part_fields() -> None:
    with pytest.raises(ValidationError, match="field status differs"):
        FieldEvidence(status="CANDIDATE_WITH_LOCATOR", candidate_value="Synthetic")
    with pytest.raises(ValidationError, match="unsupported candidate"):
        FieldEvidence(
            status="CANDIDATE_WITH_LOCATOR",
            candidate_value="Synthetic\nraw text",
            evidence_locator="synthetic heading line 1",
        )
    with pytest.raises(ValidationError, match="part field inventory differs"):
        PartEvidence(part_key="part-1", fields={}, blockers=[])


@pytest.mark.parametrize("mismatch", ["material", "raw", "text", "version_raw"])
def test_bound_part_requires_exact_original_and_version_hash(mismatch: str) -> None:
    material_id = uuid4()
    preparation_id = uuid4()
    version_id = uuid4()
    original_sha = "a" * 64
    text_sha = "b" * 64
    candidate = {"text_start": 0, "text_end": 4, "text_sha256": text_sha}
    link = {
        "id": uuid4(),
        "material_id": material_id,
        "raw_sha256": original_sha,
        "part_text_sha256": text_sha,
        "legal_version_id": version_id,
    }
    version = {"raw_sha256": original_sha, "approval_state": "REVIEW_REQUIRED"}
    if mismatch == "material":
        link["material_id"] = uuid4()
    elif mismatch == "raw":
        link["raw_sha256"] = "c" * 64
    elif mismatch == "text":
        link["part_text_sha256"] = "c" * 64
    else:
        version["raw_sha256"] = "c" * 64
    with pytest.raises(ValueError, match=r"binding differs|version checksum differs"):
        _part_evidence(
            "part-1", candidate, {"id": preparation_id}, material_id, original_sha,
            {(preparation_id, "part-1"): link}, {version_id: version},
        )


def _private_matrix() -> PackageEvidenceMatrix:
    return PackageEvidenceMatrix(
        schema_version="package-evidence.v1",
        package_key="synthetic-package",
        input_sha256="a" * 64,
        originals=[],
        editor_as_of_date=date.today(),
        editor_visible_versions=[],
        omitted_review_required_versions=[],
        snapshot_sha256="b" * 64,
    )


def test_private_output_rejects_symlink_parent_and_existing_target(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    matrix = _private_matrix()
    target = private / "matrix.json"
    sentinel = private / "sentinel.json"
    sentinel.write_text("untouched", encoding="utf-8")
    target.symlink_to(sentinel)
    with pytest.raises(FileExistsError):
        _write_private_matrix(target, matrix)
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert set(private.iterdir()) == {target, sentinel}
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink directory"):
        _write_private_matrix(linked_parent / "other.json", matrix)
    assert not (private / "other.json").exists()


def test_private_output_failed_write_leaves_no_target_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = private / "matrix.json"
    matrix = _private_matrix()
    real_fdopen = os.fdopen

    def broken_fdopen(fd, mode, *, closefd=True):
        stream = real_fdopen(fd, mode, closefd=closefd)

        class BrokenWrite:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return stream.__exit__(*args)

            def write(self, data):
                stream.write(data[:8])
                raise OSError("forced partial write")

        return BrokenWrite()

    with monkeypatch.context() as patch:
        patch.setattr(os, "fdopen", broken_fdopen)
        with pytest.raises(OSError, match="forced partial write"):
            _write_private_matrix(target, matrix)
    assert not target.exists()
    assert list(private.iterdir()) == []
    _write_private_matrix(target, matrix)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text(encoding="utf-8") == matrix.model_dump_json(indent=2) + "\n"


def test_private_output_failed_fsync_leaves_no_target_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = private / "matrix.json"
    matrix = _private_matrix()
    real_fsync = os.fsync

    def broken_fsync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("forced file fsync")
        return real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", broken_fsync)
        with pytest.raises(OSError, match="forced file fsync"):
            _write_private_matrix(target, matrix)
    assert not target.exists()
    assert list(private.iterdir()) == []
    _write_private_matrix(target, matrix)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_private_output_failed_directory_fsync_rolls_back_published_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = private / "matrix.json"
    matrix = _private_matrix()
    real_fsync = os.fsync

    def broken_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("forced directory fsync")
        return real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", broken_directory_fsync)
        with pytest.raises(OSError, match="forced directory fsync"):
            _write_private_matrix(target, matrix)
    assert not target.exists()
    assert list(private.iterdir()) == []
    _write_private_matrix(target, matrix)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_postgres_matrix_reads_exact_inventory_without_text_or_writes(tmp_path: Path) -> None:
    if os.environ.get("POSTGRES_INTEGRATION") != "1":
        pytest.skip("set POSTGRES_INTEGRATION=1 for disposable PostgreSQL")
    payload = _inventory()
    payload["package_key"] = f"synthetic-matrix-{uuid4().hex}"
    raw_by_id = {}
    for index, item in enumerate(payload["originals"]):
        raw = f"synthetic original bytes {index} {uuid4()}".encode()
        item["raw_sha256"] = hashlib.sha256(raw).hexdigest()
        raw_by_id[item["material_id"]] = raw
    request = PackageEvidenceRequest.model_validate(payload)

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                for item in request.originals:
                    raw = raw_by_id[str(item.material_id)]
                    session.add(
                        LegalReviewMaterial(
                            id=item.material_id,
                            package_key=request.package_key,
                            original_filename="synthetic.rtf",
                            title="Synthetic material",
                            kind=(
                                "CLINICAL_REFERENCE"
                                if item.kind == "CLINICAL_REFERENCE"
                                else "LEGAL_COPY"
                            ),
                            source_name="Synthetic",
                            source_url=None,
                            raw_mime_type="application/rtf",
                            raw_sha256=item.raw_sha256,
                            raw_bytes=raw,
                            raw_size_bytes=len(raw),
                            received_at=datetime.now(UTC),
                        )
                    )
                source = LegalSource(
                    source_key=f"synthetic-matrix-{uuid4().hex}",
                    revision=1,
                    display_name="Synthetic",
                    base_url="https://example.invalid/",
                    allowed_hosts=["example.invalid"],
                    trust_level="VERIFIED_COPY",
                    status="DRAFT",
                )
                session.add(source)
                await session.flush()
                first_document_id = None
                second_document_id = None
                third_document_id = None
                for number, version_id in enumerate(request.editor_visible_version_ids, start=1):
                    document = LegalDocument(
                        canonical_key=f"synthetic-matrix-{uuid4().hex}",
                        document_type="Synthetic",
                        title="Synthetic corpus record",
                        issuer="Synthetic",
                        official_number=str(number),
                    )
                    session.add(document)
                    await session.flush()
                    if number == 1:
                        first_document_id = document.id
                    elif number == 2:
                        second_document_id = document.id
                    elif number == 3:
                        third_document_id = document.id
                    raw = f"synthetic corpus {number}".encode()
                    normalized = f"synthetic normalized {number}"
                    session.add(
                        LegalVersion(
                            id=version_id,
                            document_id=document.id,
                            source_id=source.id,
                            version_no=1,
                            source_external_id=str(number),
                            source_url=f"https://example.invalid/{number}",
                            effective_from=date(2020, 1, 1),
                            approval_state="REVIEW_REQUIRED",
                            artifact_kind="NORMALIZED_EXCERPT",
                            raw_sha256=hashlib.sha256(raw).hexdigest(),
                            raw_mime_type="application/rtf",
                            raw_bytes=raw,
                            raw_size_bytes=len(raw),
                            normalized_text=normalized,
                            normalized_sha256=hashlib.sha256(normalized.encode()).hexdigest(),
                            fragments_sha256="a" * 64,
                            normalization_scope="SELECTED_EXCERPT",
                            parser_version="synthetic",
                            regression_passed=False,
                        )
                    )
            one = request.originals[7]
            normalized = "synthetic partial extraction"
            async with factory() as session, session.begin():
                await store_preparation(
                    session,
                    one.material_id,
                    MaterialPreparationInput.model_validate(
                        {
                            "raw_sha256": one.raw_sha256,
                            "title": "Synthetic preparation",
                            "kind": "NORMATIVE",
                            "group_key": "labour",
                            "parser_version": "synthetic",
                            "parts": [
                                {
                                    "part_key": "part-1",
                                    "title": "Synthetic legal title",
                                    "document_type": "Synthetic act",
                                    "evidence": {
                                        "title": "Synthetic heading line 1",
                                        "document_type": "Synthetic heading line 1",
                                    },
                                }
                            ],
                            "extraction_scope": "PARTIAL",
                            "normalized_text": normalized,
                            "normalized_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
                            "limitations": ["Synthetic table completeness unverified"],
                        }
                    ),
                )
            async with factory() as session:
                before = await session.scalar(
                    select(func.count())
                    .select_from(LegalMaterialPreparation)
                    .where(LegalMaterialPreparation.material_id == one.material_id)
                )
            sql_statements: list[str] = []

            def record_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
                sql_statements.append(statement)

            event.listen(engine.sync_engine, "before_cursor_execute", record_sql)
            try:
                matrix = await generate_evidence_matrix(factory, request)
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", record_sql)
            assert sql_statements[0].startswith(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            assert all(
                statement.lstrip().upper().startswith(("SET TRANSACTION", "SELECT"))
                for statement in sql_statements
            )
            assert all(
                "raw_bytes" not in statement and "normalized_text" not in statement
                for statement in sql_statements
            )
            validate_evidence_matrix(request, matrix)
            assert len(matrix.originals) == 58
            assert sum(len(item.parts) for item in matrix.originals) == 54
            assert len(matrix.editor_visible_versions) == 6
            assert matrix.omitted_review_required_versions == []
            assert matrix.originals[7].extraction_scope == "PARTIAL"
            assert "TEXT_COMPLETENESS_UNVERIFIED" in matrix.originals[7].blockers
            assert "SOURCE_HEADING_UNVERIFIED" in matrix.originals[7].blockers
            assert "PART_TEXT_SCOPE_UNVERIFIED" in matrix.originals[7].parts[0].blockers
            assert (
                matrix.originals[7].parts[0].fields["title"].candidate_value
                == "Synthetic legal title"
            )
            assert matrix.originals[7].parts[0].fields["title"].evidence_locator == (
                "Synthetic heading line 1"
            )
            assert all(
                item.binding_state == "NO_PREPARED_PART_BINDING"
                for item in matrix.editor_visible_versions
            )
            assert "synthetic partial extraction" not in matrix.model_dump_json()
            assert "Synthetic material" not in matrix.model_dump_json()
            async with factory() as session:
                after = await session.scalar(
                    select(func.count())
                    .select_from(LegalMaterialPreparation)
                    .where(LegalMaterialPreparation.material_id == one.material_id)
                )
            assert after == before == 1
            tampered = matrix.model_copy(update={"snapshot_sha256": "0" * 64})
            with pytest.raises(ValueError, match="snapshot"):
                validate_evidence_matrix(request, tampered)
            wrong_payload = json.loads(json.dumps(payload))
            wrong_payload["originals"][0]["raw_sha256"] = "0" * 64
            with pytest.raises(ValueError, match="receipt checksum"):
                await generate_evidence_matrix(
                    factory, PackageEvidenceRequest.model_validate(wrong_payload)
                )
            wrong_package = json.loads(json.dumps(payload))
            wrong_package["package_key"] = "other-synthetic-package"
            with pytest.raises(ValueError, match="package receipts differ"):
                await generate_evidence_matrix(
                    factory, PackageEvidenceRequest.model_validate(wrong_package)
                )
            inventory_path = tmp_path / "inventory.json"
            inventory_path.write_text(json.dumps(payload), encoding="utf-8")
            inventory_path.chmod(0o600)
            output_path = tmp_path / "matrix.json"
            generated = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "legal_core.package_evidence_matrix",
                    "generate",
                    str(inventory_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert generated.returncode == 0, generated.stderr
            assert generated.stdout == ""
            assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
            assert "synthetic partial extraction" not in output_path.read_text(encoding="utf-8")
            validated = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "legal_core.package_evidence_matrix",
                    "validate",
                    str(inventory_path),
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert validated.returncode == 0, validated.stderr
            assert validated.stdout == ""
            repeated = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "legal_core.package_evidence_matrix",
                    "generate",
                    str(inventory_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert repeated.returncode == 1
            assert "Synthetic legal title" not in repeated.stderr
            assert all(
                item is not None
                for item in (first_document_id, second_document_id, third_document_id)
            )
            async with factory() as session, session.begin():
                first_new = _synthetic_corpus_version(first_document_id, source.id, version_no=2)
                second_new = _synthetic_corpus_version(second_document_id, source.id, version_no=2)
                session.add_all([first_new, second_new])
                expired_ids = []
                for expiration in (date.today(), date(2021, 1, 1)):
                    expired_document = LegalDocument(
                        canonical_key=f"synthetic-matrix-{uuid4().hex}",
                        document_type="Synthetic",
                        title="Synthetic expired record",
                        issuer="Synthetic",
                    )
                    session.add(expired_document)
                    await session.flush()
                    expired = _synthetic_corpus_version(
                        expired_document.id, source.id, version_no=1,
                        effective_to=expiration,
                    )
                    expired_ids.append(expired.id)
                    session.add(expired)
            with pytest.raises(ValueError, match="editor-visible"):
                await generate_evidence_matrix(factory, request)
            corrected_payload = json.loads(json.dumps(payload))
            corrected_payload["editor_visible_version_ids"][:2] = [
                str(first_new.id), str(second_new.id),
            ]
            corrected_request = PackageEvidenceRequest.model_validate(corrected_payload)
            corrected_matrix = await generate_evidence_matrix(factory, corrected_request)
            omitted = corrected_matrix.omitted_review_required_versions
            assert corrected_matrix.editor_as_of_date == date.today()
            assert len(omitted) == 4
            assert {item.version_id for item in omitted} == {
                request.editor_visible_version_ids[0],
                request.editor_visible_version_ids[1],
                *expired_ids,
            }
            assert {item.exclusion_reason for item in omitted} == {
                "SUPERSEDED_BY_NEWER_VERSION", "EXPIRED",
            }
            assert sum(item.exclusion_reason == "EXPIRED" for item in omitted) == 2
            assert "Synthetic expired record" not in corrected_matrix.model_dump_json()
            assert "synthetic corpus text" not in corrected_matrix.model_dump_json()
            duplicated_omission = corrected_matrix.model_copy(
                update={"omitted_review_required_versions": [*omitted, omitted[0]]}
            )
            with pytest.raises(ValueError, match="entries differ"):
                validate_evidence_matrix(corrected_request, duplicated_omission)
            assert third_document_id is not None
            blocked_newer = _synthetic_corpus_version(
                third_document_id, source.id, version_no=2,
            )
            blocked_newer.approval_state = "BLOCKED"
            async with factory() as session, session.begin():
                session.add(blocked_newer)
            with pytest.raises(ValueError, match="editor-visible"):
                await generate_evidence_matrix(factory, corrected_request)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
