"""Operator-only, read-only evidence gap matrix for the fixed 58-file package.

No legal text is returned, no document identity is inferred, and no approval occurs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.contracts import ContractModel
from legal_core.database import create_engine, create_session_factory
from legal_core.material_preparation import Digest
from legal_core.models import (
    LegalMaterialPreparation,
    LegalPreparedPartVersion,
    LegalReferenceReviewEvent,
    LegalReviewMaterial,
    LegalVersion,
)

PackageKind = Literal["NORMATIVE", "CLINICAL_REFERENCE", "REFERENCE_FORM"]
PackageGroup = Literal[
    "clinical", "labour", "courts", "privacy", "licensing", "healthcare", "general"
]
_GROUP_COUNTS = {
    "clinical": 7,
    "labour": 7,
    "courts": 10,
    "privacy": 4,
    "licensing": 3,
    "healthcare": 22,
    "general": 5,
}
_FIELDS = (
    "title",
    "canonical_key",
    "document_type",
    "issuer",
    "official_number",
    "adoption_date",
    "publication_date",
    "version_date",
    "effective_from",
    "effective_to",
)
_REQUIRED_FIELDS = frozenset(_FIELDS) - {"effective_to"}
_PART_BLOCKERS = frozenset(
    {"PART_NOT_PREPARED", "PART_TEXT_SCOPE_UNVERIFIED", "PART_UNBOUND"}
    | {f"FIELD_{name.upper()}_UNVERIFIED" for name in _FIELDS}
)
_ORIGINAL_BLOCKERS = frozenset({
    "PREPARATION_MISSING", "TEXT_COMPLETENESS_UNVERIFIED",
    "SOURCE_HEADING_UNVERIFIED", "REFERENCE_NOT_REVIEWED",
})
FieldStatus = Literal["MISSING", "CANDIDATE_NO_LOCATOR", "CANDIDATE_WITH_LOCATOR", "NOT_APPLICABLE"]
ApprovalState = Literal["REVIEW_REQUIRED", "APPROVED", "BLOCKED"]


class ExpectedOriginal(ContractModel):
    material_id: UUID
    raw_sha256: Digest
    kind: PackageKind
    group_key: PackageGroup
    expected_part_keys: list[str] = Field(max_length=4)

    @model_validator(mode="after")
    def valid_part_shape(self) -> ExpectedOriginal:
        keys = self.expected_part_keys
        if self.kind == "CLINICAL_REFERENCE" and self.group_key != "clinical":
            raise ValueError("clinical reference group mismatch")
        if self.kind == "REFERENCE_FORM" and self.group_key != "healthcare":
            raise ValueError("reference form group mismatch")
        if self.kind == "NORMATIVE" and self.group_key == "clinical":
            raise ValueError("normative material cannot be clinical")
        if self.kind == "NORMATIVE":
            if keys != [f"part-{index}" for index in range(1, len(keys) + 1)] or not keys:
                raise ValueError("normative part keys must be contiguous from part-1")
        elif keys:
            raise ValueError("references cannot have normative parts")
        return self


class PackageEvidenceRequest(ContractModel):
    schema_version: Literal["package-evidence.v1"]
    package_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    originals: list[ExpectedOriginal] = Field(min_length=58, max_length=58)
    legacy_version_ids: list[UUID] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def fixed_package_shape(self) -> PackageEvidenceRequest:
        items = self.originals
        if len({item.material_id for item in items}) != 58 or (
            len({item.raw_sha256 for item in items}) != 58 or len(set(self.legacy_version_ids)) != 6
        ):
            raise ValueError("duplicate material, original hash or legacy version")
        if Counter(item.group_key for item in items) != _GROUP_COUNTS:
            raise ValueError("seven-group original inventory differs")
        if sum(item.kind == "NORMATIVE" for item in items) != 50 or (
            sum(item.kind == "CLINICAL_REFERENCE" for item in items) != 7
            or sum(item.kind == "REFERENCE_FORM" for item in items) != 1
            or sum(len(item.expected_part_keys) for item in items) != 54
        ):
            raise ValueError("50/54/8 package inventory differs")
        general = sorted(
            len(item.expected_part_keys) for item in items if item.group_key == "general"
        )
        if general != [1, 1, 1, 2, 4] or any(
            len(item.expected_part_keys) != 1
            for item in items
            if item.kind == "NORMATIVE" and item.group_key != "general"
        ):
            raise ValueError("code-part inventory differs")
        return self


class FieldEvidence(ContractModel):
    status: FieldStatus
    candidate_value: str | None = Field(default=None, max_length=1000)
    evidence_locator: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def consistent_status(self) -> FieldEvidence:
        if self.candidate_value != _public_metadata(self.candidate_value) or (
            self.evidence_locator != _public_metadata(self.evidence_locator)
        ):
            raise ValueError("invalid public metadata field")
        if self.status == "CANDIDATE_WITH_LOCATOR":
            valid = self.candidate_value is not None and self.evidence_locator is not None
        elif self.status == "CANDIDATE_NO_LOCATOR":
            valid = self.candidate_value is not None and self.evidence_locator is None
        else:
            valid = self.candidate_value is None
        if not valid:
            raise ValueError("field status differs from candidate and locator")
        return self


class PartEvidence(ContractModel):
    part_key: str
    text_start: int | None = None
    text_end: int | None = None
    text_sha256: Digest | None = None
    fields: dict[str, FieldEvidence]
    binding_id: UUID | None = None
    version_id: UUID | None = None
    approval_state: ApprovalState | None = None
    blockers: list[str]

    @model_validator(mode="after")
    def consistent_part(self) -> PartEvidence:
        if set(self.fields) != set(_FIELDS):
            raise ValueError("part field inventory differs")
        if len(self.blockers) != len(set(self.blockers)) or (
            not set(self.blockers) <= _PART_BLOCKERS
        ):
            raise ValueError("invalid part blockers")
        binding_fields = (self.binding_id, self.version_id, self.approval_state)
        if any(value is not None for value in binding_fields) and not all(
            value is not None for value in binding_fields
        ):
            raise ValueError("incomplete part binding")
        scope = (self.text_start, self.text_end, self.text_sha256)
        if any(value is not None for value in scope) and (
            self.text_start is None
            or self.text_end is None
            or self.text_sha256 is None
            or self.text_start < 0
            or self.text_end <= self.text_start
        ):
            raise ValueError("incomplete part text scope")
        return self


class OriginalEvidence(ContractModel):
    material_id: UUID
    raw_sha256: Digest
    kind: PackageKind
    group_key: PackageGroup
    preparation_id: UUID | None = None
    preparation_revision: int | None = None
    preparation_sha256: Digest | None = None
    extraction_scope: str | None = None
    normalized_sha256: Digest | None = None
    limitation_count: int = 0
    source_url: str | None = None
    source_locator: str | None = None
    completeness_locator: str | None = None
    parts: list[PartEvidence]
    reference_review_event_id: UUID | None = None
    blockers: list[str]

    @model_validator(mode="after")
    def consistent_original(self) -> OriginalEvidence:
        if len(self.blockers) != len(set(self.blockers)) or (
            not set(self.blockers) <= _ORIGINAL_BLOCKERS
        ):
            raise ValueError("invalid original blockers")
        for value in (self.source_url, self.source_locator, self.completeness_locator):
            if value != _public_metadata(value):
                raise ValueError("invalid public source locator")
        if self.reference_review_event_id is not None and self.preparation_id is None:
            raise ValueError("reference review without preparation")
        return self


class LegacyEvidence(ContractModel):
    version_id: UUID
    raw_sha256: Digest
    approval_state: ApprovalState
    existing_binding_id: UUID | None = None
    blocker: Literal["LEGACY_UNLINKED", "EXISTING_BINDING_REQUIRES_REVIEW"]

    @model_validator(mode="after")
    def consistent_legacy(self) -> LegacyEvidence:
        if (self.existing_binding_id is None) != (self.blocker == "LEGACY_UNLINKED"):
            raise ValueError("legacy binding state differs from blocker")
        return self


class PackageEvidenceMatrix(ContractModel):
    schema_version: Literal["package-evidence.v1"]
    package_key: str
    input_sha256: Digest
    originals: list[OriginalEvidence]
    legacy_versions: list[LegacyEvidence]
    snapshot_sha256: Digest


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _public_metadata(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if (
        not isinstance(value, str)
        or len(value) > 1000
        or any(char in value for char in ("\x00", "\n", "\r"))
    ):
        raise ValueError("unsupported candidate or locator type")
    return value


def _field(value: Any, locator: Any, *, optional: bool = False) -> FieldEvidence:
    candidate_value = _public_metadata(value)
    evidence_locator = _public_metadata(locator)
    status: FieldStatus
    if candidate_value is None:
        status = "NOT_APPLICABLE" if optional else "MISSING"
    elif evidence_locator is None:
        status = "CANDIDATE_NO_LOCATOR"
    else:
        status = "CANDIDATE_WITH_LOCATOR"
    return FieldEvidence(
        status=status,
        candidate_value=candidate_value,
        evidence_locator=evidence_locator,
    )


def _part_evidence(
    key: str,
    candidate: dict[str, Any] | None,
    prep: Any,
    material_id: UUID,
    raw_sha256: str,
    bound: dict[tuple[UUID, str], Any],
    versions: dict[UUID, Any],
) -> PartEvidence:
    evidence = candidate.get("evidence", {}) if candidate else {}
    if not isinstance(evidence, dict):
        raise ValueError("invalid field evidence")
    fields = {
        name: _field(
            candidate.get(name) if candidate else None,
            evidence.get(name),
            optional=name not in _REQUIRED_FIELDS,
        )
        for name in _FIELDS
    }
    blockers = [] if candidate else ["PART_NOT_PREPARED"]
    blockers.extend(
        f"FIELD_{name.upper()}_UNVERIFIED"
        for name in _FIELDS
        if (name in _REQUIRED_FIELDS and fields[name].status != "CANDIDATE_WITH_LOCATOR")
        or fields[name].status == "CANDIDATE_NO_LOCATOR"
    )
    if candidate is not None and any(
        candidate.get(name) is None for name in ("text_start", "text_end", "text_sha256")
    ):
        blockers.append("PART_TEXT_SCOPE_UNVERIFIED")
    link = bound.get((prep["id"], key)) if prep is not None else None
    if link is None:
        blockers.append("PART_UNBOUND")
    elif (
        candidate is None
        or link["material_id"] != material_id
        or link["raw_sha256"] != raw_sha256
        or link["part_text_sha256"] != candidate.get("text_sha256")
    ):
        raise ValueError("part binding differs from current preparation")
    version = versions.get(link["legal_version_id"]) if link else None
    if link and version is None:
        raise ValueError("bound version missing")
    if version and version["raw_sha256"] != raw_sha256:
        raise ValueError("bound version checksum differs from original")
    return PartEvidence(
        part_key=key,
        text_start=candidate.get("text_start") if candidate else None,
        text_end=candidate.get("text_end") if candidate else None,
        text_sha256=candidate.get("text_sha256") if candidate else None,
        fields=fields,
        binding_id=link["id"] if link else None,
        version_id=link["legal_version_id"] if link else None,
        approval_state=version["approval_state"] if version else None,
        blockers=blockers,
    )


def _original_evidence(
    expected: ExpectedOriginal,
    receipt: Any,
    prep: Any,
    bound: dict[tuple[UUID, str], Any],
    reviewed: dict[UUID, UUID],
    versions: dict[UUID, Any],
) -> OriginalEvidence:
    receipt_kind = "CLINICAL_REFERENCE" if expected.kind == "CLINICAL_REFERENCE" else "LEGAL_COPY"
    if receipt["raw_sha256"] != expected.raw_sha256 or receipt["kind"] != receipt_kind:
        raise ValueError("receipt checksum or kind differs from inventory")
    blockers: list[str] = []
    metadata: dict[str, Any] = {}
    if prep is None:
        blockers.append("PREPARATION_MISSING")
    else:
        if (
            prep["raw_sha256"] != expected.raw_sha256
            or prep["kind"] != expected.kind
            or prep["group_key"] != expected.group_key
        ):
            raise ValueError("current preparation differs from inventory")
        metadata = prep["metadata_json"]
        if not isinstance(metadata, dict):
            raise ValueError("invalid preparation metadata")
        if expected.kind == "NORMATIVE" and metadata.get("extraction_scope") != "FULL_DOCUMENT":
            blockers.append("TEXT_COMPLETENESS_UNVERIFIED")
        if expected.kind == "NORMATIVE" and (
            not metadata.get("source_url") or not metadata.get("source_locator")
        ):
            blockers.append("SOURCE_HEADING_UNVERIFIED")
    declared = metadata.get("parts", [])
    limitations = metadata.get("limitations", [])
    if not isinstance(declared, list) or any(not isinstance(p, dict) for p in declared):
        raise ValueError("invalid preparation part inventory")
    if not isinstance(limitations, list):
        raise ValueError("invalid extraction limitations")
    parts_by_key = {part.get("part_key"): part for part in declared}
    if len(parts_by_key) != len(declared) or not set(parts_by_key) <= set(
        expected.expected_part_keys
    ):
        raise ValueError("unexpected or duplicate prepared part")
    parts = [
        _part_evidence(
            key, parts_by_key.get(key), prep, expected.material_id, expected.raw_sha256,
            bound, versions,
        )
        for key in expected.expected_part_keys
    ]
    if expected.kind != "NORMATIVE" and prep is not None and prep["id"] not in reviewed:
        blockers.append("REFERENCE_NOT_REVIEWED")
    return OriginalEvidence(
        material_id=expected.material_id,
        raw_sha256=expected.raw_sha256,
        kind=expected.kind,
        group_key=expected.group_key,
        preparation_id=prep["id"] if prep else None,
        preparation_revision=prep["revision"] if prep else None,
        preparation_sha256=prep["preparation_sha256"] if prep else None,
        extraction_scope=metadata.get("extraction_scope"),
        normalized_sha256=metadata.get("normalized_sha256"),
        limitation_count=len(limitations),
        source_url=_public_metadata(metadata.get("source_url")),
        source_locator=_public_metadata(metadata.get("source_locator")),
        completeness_locator=_public_metadata(metadata.get("completeness_locator")),
        parts=parts,
        reference_review_event_id=reviewed.get(prep["id"]) if prep else None,
        blockers=blockers,
    )


async def _read_snapshot(
    session: AsyncSession, request: PackageEvidenceRequest
) -> PackageEvidenceMatrix:
    ids = [item.material_id for item in request.originals]
    receipt_rows = (
        (
            await session.execute(
                select(
                    LegalReviewMaterial.id,
                    LegalReviewMaterial.raw_sha256,
                    LegalReviewMaterial.kind,
                ).where(LegalReviewMaterial.package_key == request.package_key)
            )
        )
        .mappings()
        .all()
    )
    receipts = {row["id"]: row for row in receipt_rows}
    if len(receipts) != 58 or set(receipts) != set(ids):
        raise ValueError("package receipts differ from fixed inventory")
    preparations = (
        (
            await session.execute(
                select(
                    LegalMaterialPreparation.id,
                    LegalMaterialPreparation.material_id,
                    LegalMaterialPreparation.raw_sha256,
                    LegalMaterialPreparation.revision,
                    LegalMaterialPreparation.preparation_sha256,
                    LegalMaterialPreparation.kind,
                    LegalMaterialPreparation.group_key,
                    LegalMaterialPreparation.metadata_json,
                )
                .where(LegalMaterialPreparation.material_id.in_(ids))
                .order_by(
                    LegalMaterialPreparation.material_id,
                    LegalMaterialPreparation.revision.desc(),
                )
            )
        )
        .mappings()
        .all()
    )
    current: dict[UUID, Any] = {}
    for row in preparations:
        current.setdefault(row["material_id"], row)
    preparation_ids = [row["id"] for row in current.values()]
    bindings = (
        (
            await session.execute(
                select(
                    LegalPreparedPartVersion.id,
                    LegalPreparedPartVersion.material_id,
                    LegalPreparedPartVersion.preparation_id,
                    LegalPreparedPartVersion.raw_sha256,
                    LegalPreparedPartVersion.part_key,
                    LegalPreparedPartVersion.part_text_sha256,
                    LegalPreparedPartVersion.legal_version_id,
                ).where(LegalPreparedPartVersion.preparation_id.in_(preparation_ids))
            )
        )
        .mappings()
        .all()
    )
    bound = {(row["preparation_id"], row["part_key"]): row for row in bindings}
    expected_bindings = {
        (current[item.material_id]["id"], key)
        for item in request.originals
        if item.material_id in current
        for key in item.expected_part_keys
    }
    if not set(bound) <= expected_bindings:
        raise ValueError("binding outside fixed current part inventory")
    reviews = (
        (
            await session.execute(
                select(
                    LegalReferenceReviewEvent.id,
                    LegalReferenceReviewEvent.preparation_id,
                ).where(LegalReferenceReviewEvent.preparation_id.in_(preparation_ids))
            )
        )
        .mappings()
        .all()
    )
    reviewed = {row["preparation_id"]: row["id"] for row in reviews}
    versions = (
        (
            await session.execute(
                select(
                    LegalVersion.id,
                    LegalVersion.raw_sha256,
                    LegalVersion.approval_state,
                ).where(
                    LegalVersion.id.in_(
                        request.legacy_version_ids + [row["legal_version_id"] for row in bindings]
                    )
                )
            )
        )
        .mappings()
        .all()
    )
    version_by_id = {row["id"]: row for row in versions}
    legacy_bindings = (
        (
            await session.execute(
                select(
                    LegalPreparedPartVersion.id,
                    LegalPreparedPartVersion.legal_version_id,
                ).where(LegalPreparedPartVersion.legal_version_id.in_(request.legacy_version_ids))
            )
        )
        .mappings()
        .all()
    )
    legacy_bound = {row["legal_version_id"]: row["id"] for row in legacy_bindings}
    originals = [
        _original_evidence(
            expected,
            receipts[expected.material_id],
            current.get(expected.material_id),
            bound,
            reviewed,
            version_by_id,
        )
        for expected in request.originals
    ]
    legacy: list[LegacyEvidence] = []
    for version_id in request.legacy_version_ids:
        legacy_row = version_by_id.get(version_id)
        if legacy_row is None:
            raise ValueError("legacy version missing")
        existing_binding = legacy_bound.get(version_id)
        legacy.append(
            LegacyEvidence(
                version_id=version_id,
                raw_sha256=legacy_row["raw_sha256"],
                approval_state=legacy_row["approval_state"],
                existing_binding_id=existing_binding,
                blocker=(
                    "EXISTING_BINDING_REQUIRES_REVIEW" if existing_binding else "LEGACY_UNLINKED"
                ),
            )
        )
    matrix = PackageEvidenceMatrix(
        schema_version="package-evidence.v1",
        package_key=request.package_key,
        input_sha256=_digest(request.model_dump(mode="json")),
        originals=originals,
        legacy_versions=legacy,
        snapshot_sha256="0" * 64,
    )
    return matrix.model_copy(
        update={
            "snapshot_sha256": _digest(matrix.model_dump(mode="json", exclude={"snapshot_sha256"}))
        }
    )


async def generate_evidence_matrix(
    factory: async_sessionmaker[AsyncSession],
    request: PackageEvidenceRequest,
) -> PackageEvidenceMatrix:
    """Use one repeatable-read, SQL-enforced read-only snapshot; never fetch raw bytes."""
    async with factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        matrix = await _read_snapshot(session, request)
    validate_evidence_matrix(request, matrix)
    return matrix


def validate_evidence_matrix(
    request: PackageEvidenceRequest,
    matrix: PackageEvidenceMatrix,
) -> None:
    """Verify output shape, request identity and deterministic snapshot checksum."""
    if matrix.schema_version != request.schema_version or (
        matrix.package_key != request.package_key
        or matrix.input_sha256 != _digest(request.model_dump(mode="json"))
        or len(matrix.originals) != 58
        or len(matrix.legacy_versions) != 6
    ):
        raise ValueError("matrix inventory or request digest differs")
    expected = {item.material_id: item for item in request.originals}
    if (
        len({item.material_id for item in matrix.originals}) != 58
        or any(
            row.material_id not in expected
            or row.raw_sha256 != expected[row.material_id].raw_sha256
            or row.kind != expected[row.material_id].kind
            or row.group_key != expected[row.material_id].group_key
            or [part.part_key for part in row.parts] != expected[row.material_id].expected_part_keys
            for row in matrix.originals
        )
        or [row.version_id for row in matrix.legacy_versions] != request.legacy_version_ids
    ):
        raise ValueError("matrix entries differ from exact inventory")
    if matrix.snapshot_sha256 != _digest(
        matrix.model_dump(
            mode="json",
            exclude={"snapshot_sha256"},
        )
    ):
        raise ValueError("matrix snapshot checksum differs")


def _read_private_json(path: Path, limit: int) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > limit
        ):
            raise ValueError("expected an owned private regular file within size limit")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("private JSON exceeds size limit")
        return json.loads(data)
    finally:
        os.close(fd)


def load_private_request(path: Path) -> PackageEvidenceRequest:
    return PackageEvidenceRequest.model_validate(_read_private_json(path, 100_000))


def _load_private_matrix(path: Path) -> PackageEvidenceMatrix:
    return PackageEvidenceMatrix.model_validate(_read_private_json(path, 2_000_000))


def _write_private_matrix(path: Path, matrix: PackageEvidenceMatrix) -> None:
    if not path.is_absolute() or os.path.realpath(path.parent) != str(path.parent):
        raise ValueError("output path must use an absolute non-symlink directory")
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(parent_fd)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o077:
            raise ValueError("output directory must be owned and private")
        payload = (matrix.model_dump_json(indent=2) + "\n").encode()
        if len(payload) > 2_000_000:
            raise ValueError("matrix exceeds size limit")
        fd = os.open(
            path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


async def _generate(request: PackageEvidenceRequest) -> PackageEvidenceMatrix:
    engine = create_engine()
    try:
        return await generate_evidence_matrix(create_session_factory(engine), request)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Private read-only legal evidence gap matrix")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("inventory", type=Path)
    generate.add_argument("--output", required=True, type=Path)
    validate = commands.add_parser("validate")
    validate.add_argument("inventory", type=Path)
    validate.add_argument("matrix", type=Path)
    args = parser.parse_args()
    try:
        request = load_private_request(args.inventory)
        if args.command == "generate":
            matrix = asyncio.run(_generate(request))
            _write_private_matrix(args.output, matrix)
        else:
            validate_evidence_matrix(request, _load_private_matrix(args.matrix))
    except Exception:
        # Database/Pydantic errors may contain candidate legal text. Never print them.
        print("package evidence operation failed; no database writes performed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
