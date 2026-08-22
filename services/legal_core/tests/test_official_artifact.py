import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from legal_core.corpus_loader import load_manifest
from legal_core.official_artifact import (
    OFFICIAL_PROFILES,
    OfficialProfile,
    PdfSnapshot,
    inspect_pdf,
    normalize_pdf_text,
    prepare_official_manifest,
)


def _accept_test_pdf(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> None:
    current = OFFICIAL_PROFILES["736"]
    monkeypatch.setitem(
        OFFICIAL_PROFILES,
        "736",
        OfficialProfile(
            official_number=current.official_number,
            eo_number=current.eo_number,
            adoption_date=current.adoption_date,
            publication_date=current.publication_date,
            effective_from=current.effective_from,
            effective_to=current.effective_to,
            expected_page_count=current.expected_page_count,
            expected_size_bytes=len(raw),
            expected_sha256=hashlib.sha256(raw).hexdigest(),
        ),
    )


def _base_manifest(tmp_path: Path, fragment_text: str) -> Path:
    artifact_text = (
        "Постановление Правительства Российской Федерации от 11 мая 2023 г. № 736. "
        + fragment_text
    )
    path = tmp_path / "base.json"
    path.write_text(
        json.dumps(
            {
                "manifest_version": "dental-legal-corpus.v1",
                "source_key": "government-ru",
                "source_name": "Официальный портал Правительства России",
                "source_url": "https://government.ru/docs/all/147526/",
                "source_external_id": "government-pp-736",
                "allowed_hosts": ["government.ru"],
                "document_key": "ru-government-decree-736-2023",
                "document_type": "GOVERNMENT_DECREE",
                "title": "Постановление Правительства Российской Федерации от 11.05.2023 № 736",
                "issuer": "Правительство Российской Федерации",
                "official_number": "736",
                "adoption_date": "2023-05-11",
                "publication_date": "2023-05-12",
                "version_date": "2023-05-11",
                "effective_from": "2023-09-01",
                "effective_to": "2026-09-01",
                "approval_state": "REVIEW_REQUIRED",
                "artifact_kind": "NORMALIZED_EXCERPT",
                "artifact_mime_type": "application/vnd.dental-legal.normalized-excerpt+json",
                "artifact_sha256": hashlib.sha256(artifact_text.encode()).hexdigest(),
                "artifact_text": artifact_text,
                "fragments": [
                    {
                        "ordinal": 1,
                        "article": None,
                        "part": None,
                        "point": "24",
                        "heading": "Проверяемый пункт",
                        "structural_path": "Правила/пункт 24",
                        "text": fragment_text,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_prepare_official_manifest_snapshots_exact_pdf_and_never_approves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fragment = "Исполнитель письменно уведомляет потребителя до заключения договора."
    normalized = (
        "ПРАВИТЕЛЬСТВО РОССИЙСКОЙ ФЕДЕРАЦИИ 11 мая 2023 г. No 736. "
        f"{fragment} Полный официальный текст продолжается."
    )
    pdf = tmp_path / "download.pdf"
    raw = b"%PDF-1.7\nexact official bytes\n%%EOF\n"
    pdf.write_bytes(raw)
    _accept_test_pdf(monkeypatch, raw)
    monkeypatch.setattr(
        "legal_core.official_artifact.inspect_pdf",
        lambda _: PdfSnapshot(page_count=18, normalized_text=normalized),
    )

    result = prepare_official_manifest(
        pdf_path=pdf,
        base_manifest_path=_base_manifest(tmp_path, fragment),
        output_directory=tmp_path / "prepared",
        retrieved_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    )

    manifest = load_manifest(result)
    assert manifest.manifest_version == "dental-legal-corpus.v2"
    assert manifest.approval_state == "REVIEW_REQUIRED"
    assert manifest.artifact_kind == "OFFICIAL_RAW"
    assert manifest.artifact_sha256 == hashlib.sha256(raw).hexdigest()
    assert manifest.artifact_size_bytes == len(raw)
    assert manifest.artifact_page_count == 18
    assert manifest.source_key == "publication-pravo-gov-ru"
    assert manifest.source_revision == 2
    assert manifest.source_base_url == "https://publication.pravo.gov.ru/"
    assert manifest.source_external_id == "0001202305120025"
    assert manifest.normalized_text == normalized
    assert (result.parent / str(manifest.artifact_path)).read_bytes() == raw


@pytest.mark.parametrize(
    ("page_count", "normalized", "error"),
    [
        (
            17,
            "Постановление от 11 мая 2023 г. № 736. Точный проверяемый фрагмент закона.",
            "page count",
        ),
        (18, "Постановление без номера. Точный проверяемый фрагмент закона.", "act number"),
        (18, "Постановление от 11 мая 2023 г. № 736. Другой текст.", "not an exact"),
    ],
)
def test_prepare_rejects_metadata_or_fragment_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page_count: int,
    normalized: str,
    error: str,
) -> None:
    fragment = "Точный проверяемый фрагмент закона."
    pdf = tmp_path / "download.pdf"
    raw = b"%PDF-1.7\nnot accepted without metadata\n"
    pdf.write_bytes(raw)
    _accept_test_pdf(monkeypatch, raw)
    monkeypatch.setattr(
        "legal_core.official_artifact.inspect_pdf",
        lambda _: PdfSnapshot(page_count=page_count, normalized_text=normalized),
    )

    with pytest.raises(ValueError, match=error):
        prepare_official_manifest(
            pdf_path=pdf,
            base_manifest_path=_base_manifest(tmp_path, fragment),
            output_directory=tmp_path / "prepared",
            retrieved_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        )


def test_prepare_rejects_non_pdf_before_running_external_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fragment = "Точный проверяемый фрагмент закона."
    artifact = tmp_path / "not-a-pdf.bin"
    artifact.write_bytes(b"not a PDF")
    called = False

    def unexpected(_: Path) -> PdfSnapshot:
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr("legal_core.official_artifact.inspect_pdf", unexpected)
    with pytest.raises(ValueError, match="invalid signature"):
        prepare_official_manifest(
            pdf_path=artifact,
            base_manifest_path=_base_manifest(tmp_path, fragment),
            output_directory=tmp_path / "prepared",
            retrieved_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        )
    assert called is False


def test_prepare_rejects_pdf_that_does_not_match_known_official_size_and_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fragment = "Точный проверяемый фрагмент закона."
    artifact = tmp_path / "wrong.pdf"
    artifact.write_bytes(b"%PDF-1.7\nplausible but not official\n")
    called = False

    def unexpected(_: Path) -> PdfSnapshot:
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr("legal_core.official_artifact.inspect_pdf", unexpected)
    with pytest.raises(ValueError, match="independently verified metadata"):
        prepare_official_manifest(
            pdf_path=artifact,
            base_manifest_path=_base_manifest(tmp_path, fragment),
            output_directory=tmp_path / "prepared",
            retrieved_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        )
    assert called is False


def test_pdf_text_normalization_is_stable_and_keeps_punctuation() -> None:
    assert normalize_pdf_text("  Пункт\u00a024.\n  Текст\tнормы.  ") == "Пункт 24. Текст нормы."
    assert normalize_pdf_text("№ 736") == "No 736"
    assert normalize_pdf_text("а)текст; Б)другой") == "а) текст; Б) другой"


def test_pdf_inspection_uses_marked_ocr_fallback_for_image_only_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_run(
        arguments: list[str] | tuple[str, ...], *, timeout_seconds: int = 60
    ) -> subprocess.CompletedProcess[str]:
        del timeout_seconds
        calls.append(arguments[0])
        stdout = "Pages:          18\nEncrypted:      no\n" if arguments[0] == "pdfinfo" else ""
        return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

    ocr_text = "Постановление 11 мая 2023 г. № 736. " + ("Полный текст нормы. " * 40)
    monkeypatch.setattr("legal_core.official_artifact._run_tool", fake_run)
    monkeypatch.setattr("legal_core.official_artifact._ocr_pdf", lambda _: ocr_text)

    snapshot = inspect_pdf(tmp_path / "image-only.pdf")

    assert calls == ["pdfinfo", "pdftotext"]
    assert snapshot.page_count == 18
    assert snapshot.parser_version == "tesseract-rus-300dpi-nfkc.v1"
    assert snapshot.normalized_text.startswith("Постановление")
