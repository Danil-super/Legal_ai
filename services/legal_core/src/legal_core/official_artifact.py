"""Prepare a reproducible OFFICIAL_RAW corpus manifest from a manually fetched PDF.

The official publication portal is intentionally not fetched by this module. An operator obtains
the file over their normal browser connection, then this command verifies and snapshots the exact
bytes before the separate legal-editor approval step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from legal_core.corpus_loader import (
    CorpusFragment,
    CorpusManifest,
    corpus_fragments_sha256,
    load_manifest,
    normalized_text_sha256,
)

MAX_ARTIFACT_BYTES = 50_000_000
OFFICIAL_PORTAL_ROOT = "https://publication.pravo.gov.ru/"


@dataclass(frozen=True, slots=True)
class OfficialProfile:
    official_number: str
    eo_number: str
    adoption_date: str
    publication_date: str
    effective_from: str
    effective_to: str
    expected_page_count: int
    expected_size_bytes: int
    expected_sha256: str

    @property
    def artifact_url(self) -> str:
        return f"{OFFICIAL_PORTAL_ROOT}file/pdf?eoNumber={self.eo_number}"


OFFICIAL_PROFILES: dict[str, OfficialProfile] = {
    "736": OfficialProfile(
        official_number="736",
        eo_number="0001202305120025",
        adoption_date="2023-05-11",
        publication_date="2023-05-12",
        effective_from="2023-09-01",
        effective_to="2026-09-01",
        expected_page_count=18,
        expected_size_bytes=15_335_950,
        expected_sha256="a7de08b6176991ed9320a97018156253b2e5422d4df0ed9c49e6401e39005804",
    ),
    "659": OfficialProfile(
        official_number="659",
        eo_number="0001202606010083",
        adoption_date="2026-05-30",
        publication_date="2026-06-01",
        effective_from="2026-09-01",
        effective_to="2031-09-01",
        expected_page_count=18,
        expected_size_bytes=4_162_290,
        expected_sha256="7af320a12723f96f934dfcd231e3971d80560adeeb385db058255fb1712497c2",
    ),
}


@dataclass(frozen=True, slots=True)
class PdfSnapshot:
    page_count: int
    normalized_text: str
    parser_version: str = "pdftotext-nfkc.v1"


def normalize_pdf_text(value: str) -> str:
    """Normalize extraction noise without changing letters or punctuation."""

    normalized = unicodedata.normalize("NFKC", value).replace("\u00a0", " ")
    normalized = re.sub(
        r"(?<=[\u0430-\u044f\u0451]\))(?=\S)",
        " ",
        normalized,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _run_tool(
    arguments: Sequence[str], *, timeout_seconds: int = 60
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    try:
        return subprocess.run(
            list(arguments),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
            env=environment,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"required PDF tool is not installed: {arguments[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"PDF tool timed out: {arguments[0]}") from exc
    except subprocess.CalledProcessError as exc:
        diagnostic = exc.stderr.strip()[:300]
        raise ValueError(f"PDF tool rejected the artifact: {diagnostic}") from exc


def _ocr_pdf(path: Path) -> str:
    languages = _run_tool(["tesseract", "--list-langs"]).stdout.splitlines()
    if "rus" not in {language.strip() for language in languages}:
        raise RuntimeError("Tesseract Russian language data is not installed")
    with tempfile.TemporaryDirectory(prefix="dental-legal-ocr-") as directory:
        prefix = Path(directory) / "page"
        _run_tool(
            ["pdftoppm", "-r", "300", "-gray", "-png", str(path), str(prefix)],
            timeout_seconds=180,
        )
        images = sorted(Path(directory).glob("page-*.png"))
        if not images:
            raise ValueError("PDF rasterization produced no pages")
        pages = [
            _run_tool(
                ["tesseract", str(image), "stdout", "-l", "rus", "--psm", "6"],
                timeout_seconds=120,
            ).stdout
            for image in images
        ]
    return "\n".join(pages)


def inspect_pdf(path: Path) -> PdfSnapshot:
    info = _run_tool(["pdfinfo", str(path)]).stdout
    pages_match = re.search(r"(?m)^Pages:\s+(\d+)\s*$", info)
    encrypted_match = re.search(r"(?m)^Encrypted:\s+(\S+)\s*$", info)
    if pages_match is None:
        raise ValueError("pdfinfo did not report a page count")
    if encrypted_match is not None and encrypted_match.group(1).lower() != "no":
        raise ValueError("encrypted official artifacts are not supported")
    extracted = _run_tool(
        ["pdftotext", "-enc", "UTF-8", "-nopgbrk", str(path), "-"]
    ).stdout
    normalized = normalize_pdf_text(extracted)
    if len(normalized) < 500:
        normalized = normalize_pdf_text(_ocr_pdf(path))
        parser_version = "tesseract-rus-300dpi-nfkc.v1"
    else:
        parser_version = "pdftotext-nfkc.v1"
    if len(normalized) < 500:
        raise ValueError("official PDF does not contain enough extractable text")
    return PdfSnapshot(
        page_count=int(pages_match.group(1)),
        normalized_text=normalized,
        parser_version=parser_version,
    )


def _validate_profile(
    base: CorpusManifest, profile: OfficialProfile, snapshot: PdfSnapshot
) -> None:
    expected = (
        profile.official_number,
        profile.adoption_date,
        profile.publication_date,
        profile.effective_from,
        profile.effective_to,
    )
    actual = (
        base.official_number,
        base.adoption_date.isoformat(),
        base.publication_date.isoformat(),
        base.effective_from.isoformat(),
        base.effective_to.isoformat() if base.effective_to else None,
    )
    if actual != expected:
        raise ValueError("base manifest metadata conflicts with the official profile")
    if snapshot.page_count != profile.expected_page_count:
        raise ValueError("official PDF page count does not match the published metadata")

    adoption = datetime.fromisoformat(profile.adoption_date).date()
    identity_pattern = re.compile(
        rf"{adoption.day}\s+{_russian_month(adoption.month)}\s+{adoption.year}\s*\u0433?\.?\s*"
        rf"(?:№|No)\s*{re.escape(profile.official_number)}",
        re.IGNORECASE,
    )
    if identity_pattern.search(snapshot.normalized_text) is None:
        raise ValueError("PDF text does not contain the expected act number and adoption date")


def _russian_month(month: int) -> str:
    months = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    }
    return months[month]


def _verified_fragments(
    fragments: list[CorpusFragment], normalized_text: str
) -> list[CorpusFragment]:
    verified: list[CorpusFragment] = []
    for fragment in fragments:
        exact_text = normalize_pdf_text(fragment.text)
        if exact_text not in normalized_text:
            raise ValueError(
                f"fragment {fragment.ordinal} is not an exact substring of the official PDF"
            )
        verified.append(fragment.model_copy(update={"text": exact_text}))
    return verified


def _write_once(path: Path, content: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise FileExistsError(f"refusing to overwrite different content: {path}")
        return
    path.write_bytes(content)


def prepare_official_manifest(
    *,
    pdf_path: Path,
    base_manifest_path: Path,
    output_directory: Path,
    retrieved_at: datetime,
) -> Path:
    """Validate an official PDF and create an immutable v2 manifest beside its snapshot."""

    if retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must include a UTC offset")
    if not pdf_path.is_file():
        raise ValueError("official PDF file does not exist")
    raw = pdf_path.read_bytes()
    if not raw.startswith(b"%PDF-"):
        raise ValueError("official PDF has an invalid signature")
    if not 0 < len(raw) <= MAX_ARTIFACT_BYTES:
        raise ValueError("official PDF size is outside the allowed range")

    base = load_manifest(base_manifest_path)
    profile = OFFICIAL_PROFILES.get(base.official_number)
    if profile is None:
        raise ValueError("no official artifact profile exists for this document")
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) != profile.expected_size_bytes or digest != profile.expected_sha256:
        raise ValueError("official PDF bytes do not match the independently verified metadata")
    snapshot = inspect_pdf(pdf_path)
    _validate_profile(base, profile, snapshot)
    fragments = _verified_fragments(base.fragments, snapshot.normalized_text)

    artifact_name = f"pp{profile.official_number}-{digest[:16]}.pdf"
    manifest_name = f"pp{profile.official_number}-{digest[:16]}.json"
    output_directory.mkdir(parents=True, exist_ok=True)
    artifact_path = output_directory / artifact_name
    manifest_path = output_directory / manifest_name
    _write_once(artifact_path, raw)

    payload: dict[str, Any] = base.model_dump(mode="json", exclude_none=True)
    payload.update(
        {
            "manifest_version": "dental-legal-corpus.v2",
            "source_key": "publication-pravo-gov-ru",
            "source_revision": 2,
            "source_name": "Официальное опубликование правовых актов",
            "source_base_url": OFFICIAL_PORTAL_ROOT,
            "source_url": profile.artifact_url,
            "source_external_id": profile.eo_number,
            "allowed_hosts": ["publication.pravo.gov.ru"],
            "version_date": profile.adoption_date,
            "artifact_kind": "OFFICIAL_RAW",
            "artifact_mime_type": "application/pdf",
            "artifact_sha256": digest,
            "artifact_path": artifact_name,
            "artifact_retrieved_at": retrieved_at.isoformat(),
            "artifact_size_bytes": len(raw),
            "artifact_page_count": snapshot.page_count,
            "normalized_text": snapshot.normalized_text,
            "normalized_sha256": normalized_text_sha256(snapshot.normalized_text),
            "fragments_sha256": corpus_fragments_sha256(fragments),
            "normalization_scope": "FULL_DOCUMENT",
            "parser_version": snapshot.parser_version,
            "fragments": [fragment.model_dump(mode="json") for fragment in fragments],
        }
    )
    payload.pop("artifact_text", None)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    _write_once(manifest_path, encoded)

    # Re-open the exact output pair through the production loader before reporting success.
    load_manifest(manifest_path)
    if artifact_path.read_bytes() != raw:  # pragma: no cover - filesystem integrity guard
        raise RuntimeError("official artifact snapshot changed during preparation")
    return manifest_path


def _retrieved_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a manually downloaded official PDF and prepare a v2 corpus manifest"
    )
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--base-manifest", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--retrieved-at", required=True, type=_retrieved_at)
    args = parser.parse_args()
    prepared = prepare_official_manifest(
        pdf_path=args.pdf,
        base_manifest_path=args.base_manifest,
        output_directory=args.output_directory,
        retrieved_at=args.retrieved_at,
    )
    print(f"prepared REVIEW_REQUIRED official manifest {prepared}")


if __name__ == "__main__":
    main()
