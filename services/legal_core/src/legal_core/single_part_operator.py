"""Operator-only pilot for one fully evidenced normative RTF part.

The local package is untrusted input. It never approves a source or legal version.
The default CLI path rolls its entire database transaction back.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.contracts import ContractModel
from legal_core.corpus_loader import (
    CorpusManifest,
    ingest_manifest_in_session,
    validate_artifact_bytes,
)
from legal_core.database import create_engine, create_session_factory
from legal_core.material_preparation import Digest, MaterialPreparationInput, store_preparation
from legal_core.models import LegalMaterialPreparation
from legal_core.normative_preparation import (
    bind_prepared_part,
    inspect_normative_rtf,
    prepared_part_keys_match,
)
from legal_core.preparation_import import _read_regular


class SinglePartRequest(ContractModel):
    schema_version: Literal[1]
    material_id: UUID
    actor_user_id: UUID
    raw_sha256: Digest
    part_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    part_text_sha256: Digest
    source_url: str = Field(
        pattern=r"^https://internet\.garant\.ru/document/redirect/[0-9]{1,20}/0$"
    )
    corpus_manifest_path: str = Field(min_length=1, max_length=240)
    preparation: MaterialPreparationInput


@dataclass(frozen=True)
class SinglePartPackage:
    request: SinglePartRequest
    preparation: MaterialPreparationInput
    corpus: CorpusManifest
    raw_bytes: bytes


@dataclass(frozen=True)
class SinglePartResult:
    preparation_id: UUID
    version_id: UUID
    binding_id: UUID
    committed: bool


def _json_file(root: Path, name: str) -> dict[str, object]:
    payload = json.loads(_read_regular(root, name, 30_000_000))
    if not isinstance(payload, dict):
        raise ValueError("operator manifest must be an object")
    return payload


def read_single_part_package(path: Path) -> SinglePartPackage:
    """Read fixed package files once, enforce the parser and corpus contracts."""
    return _read_part_package(path, require_single=True)


def read_prepared_part_package(path: Path) -> SinglePartPackage:
    """Validate one selected part; a batch must additionally account for all parts."""
    return _read_part_package(path, require_single=False)


def _read_part_package(path: Path, *, require_single: bool) -> SinglePartPackage:
    root = path.parent
    request = SinglePartRequest.model_validate(_json_file(root, path.name))
    corpus = CorpusManifest.model_validate(_json_file(root, request.corpus_manifest_path))
    if (
        corpus.source_key != "garant"
        or corpus.source_revision != 1
        or corpus.source_name != "Гарант"
        or corpus.source_trust_level != "VERIFIED_COPY"
        or corpus.source_base_url != "https://internet.garant.ru/"
        or corpus.allowed_hosts != ["internet.garant.ru"]
    ):
        raise ValueError("Garant source revision or profile differs from the pilot contract")
    current_copy = corpus.manifest_version == "dental-legal-corpus.v5" and not require_single
    if (not current_copy and corpus.manifest_version != "dental-legal-corpus.v4") or (
        corpus.artifact_mime_type != "application/rtf" or corpus.artifact_path is None
    ):
        raise ValueError("one-part pilot requires a v4 Garant RTF artifact")
    raw_bytes = _read_regular(root, corpus.artifact_path, 50_000_000)
    validate_artifact_bytes(corpus, raw_bytes)
    prepared = request.preparation
    if (
        prepared.kind != "NORMATIVE"
        or prepared.extraction_scope != ("PARTIAL" if current_copy else "FULL_DOCUMENT")
        or (
            not prepared.parts
            or (not current_copy and prepared.limitations)
            or (current_copy and prepared.limitations != corpus.extraction_limitations)
        )
    ):
        raise ValueError("part operation requires a complete normative preparation")
    if require_single and len(prepared.parts) != 1:
        raise ValueError("one-part pilot requires a single normative part")
    part = next((part for part in prepared.parts if part.part_key == request.part_key), None)
    if part is None:
        raise ValueError("selected prepared part is missing")
    if request.raw_sha256 != prepared.raw_sha256 or (
        request.raw_sha256 != corpus.artifact_sha256
        or request.part_key != part.part_key
        or request.part_text_sha256 != part.text_sha256
        or request.source_url != prepared.source_url
        or request.source_url != corpus.source_url
    ):
        raise ValueError("original, source or part checksum differs across manifests")
    candidate = inspect_normative_rtf(raw_bytes, prepared.normalized_text)
    if candidate.raw_sha256 != prepared.raw_sha256 or (
        candidate.title != prepared.title
        or candidate.normalized_sha256 != prepared.normalized_sha256
        or candidate.source_url != prepared.source_url
        or candidate.source_locator != prepared.source_locator
    ):
        raise ValueError("preparation does not match the P8 original parser")
    if not prepared_part_keys_match(prepared, candidate):
        raise ValueError("part keys differ from the P8 original parser")
    parsed = candidate.parts[prepared.parts.index(part)]
    if (len(prepared.parts) == 1 and part.title != parsed.title) or (
        part.text_start != parsed.text_start
        or part.text_end != parsed.text_end
        or part.text_sha256 != parsed.text_sha256
        or (parsed.document_type is not None and part.document_type != parsed.document_type)
        or (parsed.issuer is not None and part.issuer != parsed.issuer)
        or (parsed.official_number is not None and part.official_number != parsed.official_number)
        or (
            parsed.adoption_date_candidate is not None
            and part.adoption_date != parsed.adoption_date_candidate
        )
    ):
        raise ValueError("part identity or text scope differs from the P8 parser")
    required: tuple[str, ...] = (
        "title",
        "canonical_key",
        "document_type",
        "issuer",
        "official_number",
        "adoption_date",
        "publication_date",
        "version_date",
        "effective_from",
    )
    if current_copy:
        required = ("title", "canonical_key", "document_type", "adoption_date", "copy_valid_from")
    if any(
        getattr(part, field) is None or not part.evidence.get(field, "").strip()
        for field in required
    ):
        raise ValueError("part identity or dates lack field-by-field evidence")
    if (
        corpus.document_key,
        corpus.document_type,
        corpus.title,
        corpus.issuer,
        corpus.official_number,
        corpus.adoption_date,
        corpus.publication_date,
        corpus.version_date,
        corpus.effective_from,
        corpus.effective_to,
    ) != (
        part.canonical_key,
        part.document_type,
        part.title,
        part.issuer,
        part.official_number,
        part.adoption_date,
        part.publication_date,
        part.version_date,
        part.copy_valid_from if current_copy else part.effective_from,
        part.effective_to,
    ) or (
        corpus.source_external_id != request.source_url.rsplit("/", 2)[-2]
        or corpus.normalized_text != prepared.normalized_text[part.text_start : part.text_end]
        or corpus.normalized_sha256 != part.text_sha256
        or corpus.parser_version != prepared.parser_version
        or corpus.date_basis != part.date_basis
    ):
        raise ValueError("corpus manifest differs from evidenced prepared part")
    return SinglePartPackage(request, prepared, corpus, raw_bytes)


async def run_single_part_package(
    factory: async_sessionmaker[AsyncSession],
    path: Path,
    *,
    commit: bool = False,
) -> SinglePartResult:
    """Create preparation, version and exact binding atomically, or roll all back."""
    package = read_single_part_package(path)
    async with factory() as session, session.begin() as transaction:
        preparation = await store_preparation(
            session, package.request.material_id, package.preparation
        )
        latest_revision = await session.scalar(
            select(func.max(LegalMaterialPreparation.revision)).where(
                LegalMaterialPreparation.material_id == package.request.material_id
            )
        )
        if preparation.revision != latest_revision:
            raise ValueError("newer preparation revision supersedes this operator manifest")
        version_id = await ingest_manifest_in_session(session, package.corpus, package.raw_bytes)
        binding = await bind_prepared_part(
            session,
            preparation_id=preparation.id,
            part_key=package.request.part_key,
            legal_version_id=version_id,
            actor_user_id=package.request.actor_user_id,
        )
        result = SinglePartResult(preparation.id, version_id, binding.id, commit)
        if not commit:
            await transaction.rollback()
    return result


async def _run(path: Path, commit: bool) -> None:
    engine = create_engine()
    try:
        result = await run_single_part_package(create_session_factory(engine), path, commit=commit)
    finally:
        await engine.dispose()
    state = "committed" if result.committed else "dry-run rolled back"
    print(
        f"{state}: preparation={result.preparation_id} version={result.version_id} "
        f"binding={result.binding_id}; no legal approval performed"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate one evidenced legal RTF part")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--commit", action="store_true", help="persist REVIEW_REQUIRED only")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.manifest, args.commit))
    except Exception:
        # Pydantic/SQL errors may embed supplied legal text; never print them here.
        print("single-part operation failed; no approval performed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
