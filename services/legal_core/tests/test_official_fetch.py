import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from legal_core.corpus_loader import load_manifest
from legal_core.official_fetch import fetch_official_artifact
from legal_core.pravo_source import PravoPdfArtifact

ROOT = Path(__file__).parents[3]


def _manifest(tmp_path: Path, raw: bytes) -> Path:
    original = ROOT / "services/legal_core/corpus/official/pp659-7af320a12723f96f.json"
    payload = json.loads(original.read_text(encoding="utf-8"))
    payload.update(
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        artifact_size_bytes=len(raw),
        artifact_path="official.pdf",
    )
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _source(raw: bytes) -> AsyncMock:
    source = AsyncMock()
    source.fetch_pdf.return_value = PravoPdfArtifact(
        eo_number="0001202606010083",
        source_url="https://publication.pravo.gov.ru/File/Pdf?eoNumber=0001202606010083",
        content=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    return source


def test_fetch_snapshots_only_exact_bytes_and_keeps_manifest_pending(tmp_path: Path) -> None:
    raw = b"%PDF-1.7 synthetic source test"
    manifest_path = _manifest(tmp_path, raw)
    original_manifest = manifest_path.read_bytes()
    source = _source(raw)

    assert asyncio.run(fetch_official_artifact(manifest_path, source)) is True
    assert asyncio.run(fetch_official_artifact(manifest_path, source)) is False
    source.fetch_pdf.assert_awaited_once_with("0001202606010083")
    assert (tmp_path / "official.pdf").read_bytes() == raw
    assert manifest_path.read_bytes() == original_manifest
    assert load_manifest(manifest_path).approval_state == "REVIEW_REQUIRED"


def test_fetch_never_saves_a_different_official_edition(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path, b"%PDF-1.7 expected edition")
    with pytest.raises(ValueError, match="pinned manifest"):
        asyncio.run(fetch_official_artifact(manifest_path, _source(b"%PDF-1.7 newer edition")))
    assert not (tmp_path / "official.pdf").exists()


def test_fetch_never_replaces_an_existing_incorrect_artifact(tmp_path: Path) -> None:
    raw = b"%PDF-1.7 expected edition"
    manifest_path = _manifest(tmp_path, raw)
    destination = tmp_path / "official.pdf"
    destination.write_bytes(b"original bytes")
    source = _source(raw)
    with pytest.raises(ValueError, match="byte count"):
        asyncio.run(fetch_official_artifact(manifest_path, source))
    assert destination.read_bytes() == b"original bytes"
    source.fetch_pdf.assert_not_awaited()


def test_fetch_rejects_manifest_that_changes_the_official_host(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path, b"%PDF-1.7 fixture")
    payload = json.loads(manifest_path.read_text())
    payload.update(
        source_url="https://untrusted.example/file/pdf?eoNumber=0001202606010083",
        source_base_url="https://untrusted.example/",
        allowed_hosts=["untrusted.example"],
    )
    manifest_path.write_text(json.dumps(payload))
    source = _source(b"%PDF-1.7 fixture")
    with pytest.raises(ValueError, match="official portal"):
        asyncio.run(fetch_official_artifact(manifest_path, source))
    source.fetch_pdf.assert_not_awaited()


def test_fetch_rejects_insecure_source_scheme(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path, b"%PDF-1.7 fixture")
    payload = json.loads(manifest_path.read_text())
    payload["source_url"] = payload["source_url"].replace("https://", "http://")
    manifest_path.write_text(json.dumps(payload))
    source = _source(b"%PDF-1.7 fixture")
    with pytest.raises(ValueError):
        asyncio.run(fetch_official_artifact(manifest_path, source))
    source.fetch_pdf.assert_not_awaited()
