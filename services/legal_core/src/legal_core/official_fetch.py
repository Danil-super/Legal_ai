"""Fetch checksum-locked official PDFs without changing their manifests or approval state."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from legal_core.corpus_loader import CorpusManifest, load_artifact, load_manifest
from legal_core.legal_watcher import publication_source_from_environment
from legal_core.pravo_source import PRAVO_HOST, PravoPdfArtifact


class PdfSource(Protocol):
    async def fetch_pdf(self, eo_number: str) -> PravoPdfArtifact: ...


def _official_manifest(path: Path) -> CorpusManifest:
    manifest = CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))
    parsed = urlparse(manifest.source_url)
    if (
        manifest.manifest_version != "dental-legal-corpus.v2"
        or manifest.artifact_kind != "OFFICIAL_RAW"
        or manifest.artifact_mime_type != "application/pdf"
        or manifest.source_key != "publication-pravo-gov-ru"
        or manifest.allowed_hosts != [PRAVO_HOST]
        or parsed.scheme != "https"
        or parsed.hostname != PRAVO_HOST
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.path.casefold() != "/file/pdf"
        or parse_qs(parsed.query) != {"eoNumber": [manifest.source_external_id]}
    ):
        raise ValueError("fetch requires a checksum-locked official portal PDF manifest")
    return manifest


async def fetch_official_artifact(manifest_path: Path, source: PdfSource) -> bool:
    """Restore missing PDF bytes, validating existing files without redownloading them."""
    manifest_path = manifest_path.resolve()
    manifest = _official_manifest(manifest_path)
    assert manifest.artifact_path is not None
    destination = manifest_path.parent / manifest.artifact_path
    if destination.is_symlink() or not destination.resolve().is_relative_to(manifest_path.parent):
        raise ValueError("official artifact path must stay inside the manifest directory")
    if destination.exists():
        load_artifact(manifest, manifest_path)
        return False

    artifact = await source.fetch_pdf(manifest.source_external_id)
    raw = artifact.content
    if (
        artifact.eo_number != manifest.source_external_id
        or not raw.startswith(b"%PDF-")
        or len(raw) != manifest.artifact_size_bytes
        or hashlib.sha256(raw).hexdigest() != manifest.artifact_sha256
    ):
        raise ValueError("downloaded official PDF does not match the pinned manifest")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".official-download-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        try:
            os.link(temporary, destination)
            created = True
        except FileExistsError:
            created = False
        # A concurrent download must contain exactly the same immutable bytes.
        if destination.is_symlink():
            raise ValueError("official artifact must not be a symlink")
        load_manifest(manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify local PDF and manifest without networking",
    )
    args = parser.parse_args()
    try:
        if args.verify_only:
            _official_manifest(args.manifest)
            load_manifest(args.manifest)
            action = "verified"
        else:
            created = asyncio.run(
                fetch_official_artifact(args.manifest, publication_source_from_environment())
            )
            action = "downloaded and verified" if created else "verified existing"
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"official artifact unavailable: {exc}\n")
    print(f"{action} official PDF; manifest remains REVIEW_REQUIRED; no database changes")


if __name__ == "__main__":
    main()
