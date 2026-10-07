"""Generate importable, unapproved preparation revisions from immutable RTF headings.

Input is an operator-private package of current preparations and the exact original
artifacts. No source fetch, database write, effective-date inference, or approval
is performed. References retain their existing preparation byte-for-byte.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

from legal_core.material_preparation import MaterialPreparationInput, PreparedPart
from legal_core.normative_preparation import NormativePartCandidate, inspect_normative_rtf
from legal_core.preparation_import import (
    PreparationPackage,
    _read_regular,
    read_preparation_package,
)

_PARSER_VERSION = "rtf-heading-candidates.v2"
_CANDIDATE_LIMITATION = (
    "Heading/signature metadata are candidates from the supplied copy; LEGAL_EDITOR must "
    "verify identity, publication, edition, applicability, and text completeness."
)


def _enrich_part(
    previous: PreparedPart, candidate: NormativePartCandidate, normalized_sha256: str,
) -> PreparedPart:
    payload = previous.model_dump()
    evidence = dict(previous.evidence)
    locator = (
        f"Candidate only: {candidate.identity_locator}; normalized SHA-256 "
        f"{normalized_sha256}"
    )
    for field, value in (
        ("document_type", candidate.document_type), ("issuer", candidate.issuer),
        ("official_number", candidate.official_number),
        ("adoption_date", candidate.adoption_date_candidate),
    ):
        if value is None:
            continue
        existing = getattr(previous, field)
        if existing is not None and existing != value:
            raise ValueError("candidate conflicts with existing prepared identity")
        payload[field] = value
        evidence.setdefault(field, locator)
    payload["title"] = candidate.title
    evidence["title"] = locator
    payload["evidence"] = evidence
    # Candidate boundaries are deliberately not persisted as verified FULL_DOCUMENT scopes.
    return PreparedPart.model_validate(payload)


def enrich_preparation(
    raw: bytes, previous: MaterialPreparationInput,
) -> MaterialPreparationInput:
    """Populate observed identity candidates; preserve missing legal-edition metadata."""
    if hashlib.sha256(raw).hexdigest() != previous.raw_sha256:
        raise ValueError("original checksum differs from preparation receipt")
    if previous.kind != "NORMATIVE":
        return previous
    if previous.extraction_scope != "PARTIAL":
        raise ValueError("candidate enrichment requires an existing partial extraction")
    candidate = inspect_normative_rtf(raw, previous.normalized_text)
    if candidate.normalized_sha256 != previous.normalized_sha256:
        raise ValueError("normalized checksum differs from preparation receipt")
    if len(previous.parts) != len(candidate.parts):
        raise ValueError("candidate parts differ from declared preparation parts")
    parts = []
    for old, observed in zip(previous.parts, candidate.parts, strict=True):
        # Historical single-act receipts use 'document'; do not change their part identity.
        if len(previous.parts) > 1 and old.part_key != observed.part_key:
            raise ValueError("candidate part keys differ from preparation receipt")
        parts.append(_enrich_part(old, observed, candidate.normalized_sha256))
    if previous.source_url is not None and candidate.source_url is not None and (
        previous.source_url != candidate.source_url
    ):
        raise ValueError("candidate source conflicts with preparation receipt")
    payload = previous.model_dump() | {
        "title": candidate.title, "parser_version": _PARSER_VERSION,
        "parts": [part.model_dump() for part in parts],
        "limitations": list(dict.fromkeys([*previous.limitations, _CANDIDATE_LIMITATION])),
    }
    if candidate.source_url is not None:
        payload["source_url"] = candidate.source_url
        # The receipt SHA in this revision binds these exact raw byte offsets.
        payload["source_locator"] = candidate.source_locator
    return MaterialPreparationInput.model_validate(payload)


def _scope_candidates(
    name: str, raw: bytes, prepared: MaterialPreparationInput,
) -> dict[str, object]:
    """Private observations only: the importable preparation stays PARTIAL."""
    observed = inspect_normative_rtf(raw, prepared.normalized_text)
    return {
        "original_filename": name, "raw_sha256": observed.raw_sha256,
        "normalized_sha256": observed.normalized_sha256,
        "source_url": observed.source_url, "source_locator": observed.source_locator,
        "extraction_scope": prepared.extraction_scope,
        "parts": [{
            "part_key": stored.part_key, "parser_part_key": parsed.part_key,
            "text_start": parsed.text_start, "text_end": parsed.text_end,
            "text_sha256": parsed.text_sha256,
            "offset_unit": "UNICODE_CODEPOINT", "status": "UNVERIFIED_CANDIDATE",
            "blockers": list(parsed.blockers),
        } for stored, parsed in zip(prepared.parts, observed.parts, strict=True)],
    }


def _write_private(directory: int, name: str, payload: bytes) -> None:
    descriptor = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600, dir_fd=directory,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def _json(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode()


def generate_preparation_package(
    baseline_manifest: Path, originals: Path, output: Path, *, expected_count: int = 58,
) -> Path:
    """Validate every receipt, then create a new private package with manifest written last."""
    baseline, previous = read_preparation_package(baseline_manifest)
    if not 1 <= expected_count <= 200 or len(previous) != expected_count:
        raise ValueError("baseline does not account for the expected original count")
    expected_names = {name for name, _ in previous}
    if originals.is_symlink() or not originals.is_dir():
        raise ValueError("original package must be a regular directory")
    if {path.name for path in originals.iterdir()} != expected_names:
        raise ValueError("original directory does not match all preparation receipts")
    enriched = []
    scopes = []
    for name, preparation in previous:
        raw = _read_regular(originals, name, 50_000_000)
        prepared = enrich_preparation(raw, preparation)
        enriched.append((name, prepared))
        if prepared.kind == "NORMATIVE":
            scopes.append(_scope_candidates(name, raw, prepared))
    items = []
    missing: Counter[str] = Counter()
    for index, (name, preparation) in enumerate(enriched):
        items.append({
            "original_filename": name, "preparation_path": f"card-{index:03d}.json",
            "text_path": f"text-{index:03d}.txt" if preparation.normalized_text else None,
        })
        missing.update(field for part in preparation.parts for field in part.missing_fields())
    manifest = PreparationPackage.model_validate({
        "schema_version": 1, "package_key": baseline.package_key, "items": items,
    })
    parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir(output.name, mode=0o700, dir_fd=parent)
        except FileExistsError as exc:
            raise ValueError("output package already exists") from exc
        directory = os.open(
            output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent,
        )
        try:
            for index, (_, preparation) in enumerate(enriched):
                _write_private(directory, f"card-{index:03d}.json", _json(preparation.metadata()))
                if preparation.normalized_text:
                    _write_private(directory, f"text-{index:03d}.txt",
                                   preparation.normalized_text.encode())
            _write_private(directory, "preparation-summary.json", _json({
                "package_key": baseline.package_key, "originals": len(enriched),
                "normative_parts": sum(len(preparation.parts) for _, preparation in enriched),
                "kind_counts": dict(Counter(preparation.kind for _, preparation in enriched)),
                "group_counts": dict(Counter(preparation.group_key for _, preparation in enriched)),
                "extraction_scope_counts": dict(Counter(
                    preparation.extraction_scope for _, preparation in enriched
                )),
                "missing_field_counts": dict(sorted(missing.items())),
                "readiness": "UNAPPROVED; candidate metadata and partial text require review",
            }))
            _write_private(directory, "candidate-scopes.json", _json({
                "schema_version": "dental-preparation-scopes.candidates.v1",
                "package_key": baseline.package_key, "parser_version": _PARSER_VERSION,
                "status": "UNVERIFIED_CANDIDATE", "items": scopes,
            }))
            _write_private(directory, "package.json", _json(manifest.model_dump(mode="json")))
        finally:
            os.close(directory)
    finally:
        os.close(parent)
    return output / "package.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_manifest", type=Path)
    parser.add_argument("originals", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-count", type=int, default=58)
    args = parser.parse_args()
    try:
        path = generate_preparation_package(
            args.baseline_manifest, args.originals, args.output, expected_count=args.expected_count,
        )
        _, items = read_preparation_package(path)
    except (ValueError, OSError):
        print("candidate preparation failed; inspect private inputs; no database writes",
              file=sys.stderr)
        raise SystemExit(1) from None
    print(f"prepared {len(items)} unapproved material cards; no database writes or legal approval")


if __name__ == "__main__":
    main()
