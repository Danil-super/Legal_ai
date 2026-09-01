"""Validated synthetic tenant clinic documents used only for development/regression tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from legal_core.clinic_documents import normalize_clinic_document_text

_FIXTURE_ROOT = Path(__file__).parents[2] / "corpus" / "synthetic_clinic_documents"
_MANIFEST = _FIXTURE_ROOT / "manifest.v1.json"


@dataclass(frozen=True, slots=True)
class SyntheticClinicDocumentVersion:
    document_key: str
    document_type: str
    title: str
    version_no: int
    filename: str
    valid_from: date | None
    valid_to: date | None
    raw_sha256: str
    normalized_text_sha256: str
    text: str

    def applies_on(self, as_of_date: date) -> bool:
        return (
            (self.valid_from is None or self.valid_from <= as_of_date)
            and (self.valid_to is None or self.valid_to > as_of_date)
        )


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("synthetic fixture date must be an ISO string or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("synthetic fixture date is malformed") from exc


def _safe_fixture_path(filename: object) -> Path:
    if not isinstance(filename, str) or not filename.endswith(".txt"):
        raise ValueError("synthetic fixture file must be a .txt filename")
    candidate = (_FIXTURE_ROOT / filename).resolve()
    root = _FIXTURE_ROOT.resolve()
    if candidate.parent != root:
        raise ValueError("synthetic fixture path escaped fixture root")
    return candidate


def _hash_field(version: dict[object, object], key: str) -> str:
    value = version.get(key)
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"synthetic fixture {key} must be a SHA-256 hex digest")
    return value


def load_synthetic_clinic_versions() -> tuple[SyntheticClinicDocumentVersion, ...]:
    payload = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "synthetic-clinic-documents.v1":
        raise ValueError("unsupported synthetic clinic fixture manifest")
    if payload.get("purpose") != "DEVELOPMENT_AND_REGRESSION_ONLY":
        raise ValueError("synthetic clinic fixture purpose changed")
    if payload.get("authority") != "NOT_A_LEGAL_SOURCE":
        raise ValueError("synthetic clinic fixtures cannot become legal authority")
    if payload.get("tenant_status") != "NOT_A_REAL_TENANT":
        raise ValueError("synthetic clinic fixtures cannot represent a real tenant")
    if payload.get("source_policy") != (
        "STRUCTURE_INFORMED_BY_PUBLIC_REFERENCE_TAXONOMY_NO_TEXT_COPIED"
    ):
        raise ValueError("synthetic clinic fixture source policy changed")

    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("synthetic fixture manifest has no documents")

    result: list[SyntheticClinicDocumentVersion] = []
    seen_document_keys: set[str] = set()
    seen_files: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("synthetic fixture document must be an object")
        document_key = document.get("document_key")
        document_type = document.get("document_type")
        title = document.get("title")
        versions = document.get("versions")
        if not isinstance(document_key, str) or not document_key:
            raise ValueError("synthetic fixture document_key is required")
        if document_key in seen_document_keys:
            raise ValueError("synthetic fixture document_key must be unique")
        seen_document_keys.add(document_key)
        if not isinstance(document_type, str) or not document_type:
            raise ValueError("synthetic fixture document_type is required")
        if not isinstance(title, str) or not title:
            raise ValueError("synthetic fixture title is required")
        if not isinstance(versions, list) or not versions:
            raise ValueError("synthetic fixture document has no versions")

        seen_version_numbers: set[int] = set()
        for version in versions:
            if not isinstance(version, dict):
                raise ValueError("synthetic fixture version must be an object")
            version_no = version.get("version_no")
            if not isinstance(version_no, int) or version_no < 1:
                raise ValueError("synthetic fixture version_no must be positive")
            if version_no in seen_version_numbers:
                raise ValueError("synthetic fixture version_no must be unique per document")
            seen_version_numbers.add(version_no)

            path = _safe_fixture_path(version.get("file"))
            filename = path.name
            if filename in seen_files:
                raise ValueError("synthetic fixture files must be unique")
            seen_files.add(filename)
            raw_text = path.read_text(encoding="utf-8")
            if not raw_text.startswith("СИНТЕТИЧ"):
                raise ValueError("synthetic fixture must carry an explicit synthetic marker")
            if "http://" in raw_text or "https://" in raw_text:
                raise ValueError("synthetic fixture text must not embed source URLs")

            raw_sha256 = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            expected_raw_sha256 = _hash_field(version, "raw_sha256")
            if raw_sha256 != expected_raw_sha256:
                raise ValueError("synthetic fixture raw SHA-256 mismatch")

            normalized_text = normalize_clinic_document_text(raw_text)
            normalized_text_sha256 = hashlib.sha256(
                normalized_text.encode("utf-8")
            ).hexdigest()
            expected_normalized_sha256 = _hash_field(version, "normalized_text_sha256")
            if normalized_text_sha256 != expected_normalized_sha256:
                raise ValueError("synthetic fixture normalized SHA-256 mismatch")

            valid_from = _optional_date(version.get("valid_from"))
            valid_to = _optional_date(version.get("valid_to"))
            if valid_from is not None and valid_to is not None and valid_to <= valid_from:
                raise ValueError("synthetic fixture valid_to must be after valid_from")

            result.append(
                SyntheticClinicDocumentVersion(
                    document_key=document_key,
                    document_type=document_type,
                    title=title,
                    version_no=version_no,
                    filename=filename,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    raw_sha256=raw_sha256,
                    normalized_text_sha256=normalized_text_sha256,
                    text=raw_text,
                )
            )

    return tuple(result)


def synthetic_version_at(
    document_key: str,
    *,
    as_of_date: date,
) -> SyntheticClinicDocumentVersion:
    matches = [
        item
        for item in load_synthetic_clinic_versions()
        if item.document_key == document_key and item.applies_on(as_of_date)
    ]
    if len(matches) != 1:
        raise LookupError("synthetic document has no unique version for requested date")
    return matches[0]
