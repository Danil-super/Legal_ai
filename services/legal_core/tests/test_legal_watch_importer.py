import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from legal_core.database import database_url
from legal_core.legal_watch_importer import import_watch_inbox, load_staged_candidate
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _write_candidate(inbox: Path, *, eo_number: str) -> Path:
    pdf = b"%PDF-1.7\nsynthetic watcher importer artifact"
    directory = inbox / eo_number
    directory.mkdir(parents=True)
    (directory / "official.pdf").write_bytes(pdf)
    metadata = {
        "schemaVersion": "dental-legal-watch.v1",
        "status": "REVIEW_REQUIRED",
        "autoPromotionAllowed": False,
        "eoNumber": eo_number,
        "title": "Synthetic legal watcher discovery",
        "documentNumber": "659-test",
        "documentDate": "2026-05-30",
        "publicationDate": "2026-06-01",
        "sourceUrl": f"https://publication.pravo.gov.ru/File/Pdf?eoNumber={eo_number}",
        "pdfSha256": hashlib.sha256(pdf).hexdigest(),
        "pdfSizeBytes": len(pdf),
        "matchedRuleIds": ["paid-medical-services"],
        "stagedAt": "2026-08-31T14:00:00+00:00",
    }
    (directory / "candidate.json").write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )
    return directory


def _unique_eo_number() -> str:
    return f"99{uuid4().int % 10**16:016d}"


def test_candidate_loader_binds_metadata_pdf_and_quarantine_path(tmp_path: Path) -> None:
    eo_number = _unique_eo_number()
    directory = _write_candidate(tmp_path, eo_number=eo_number)

    candidate = load_staged_candidate(inbox=tmp_path, directory=directory)

    assert candidate.metadata.eo_number == eo_number
    assert candidate.quarantine_ref == f"{eo_number}/official.pdf"
    assert len(candidate.candidate_sha256) == 64


def test_candidate_loader_rejects_tampered_pdf(tmp_path: Path) -> None:
    eo_number = _unique_eo_number()
    directory = _write_candidate(tmp_path, eo_number=eo_number)
    (directory / "official.pdf").write_bytes(b"%PDF-1.7\ntampered")

    with pytest.raises(ValueError, match="size|SHA-256"):
        load_staged_candidate(inbox=tmp_path, directory=directory)


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL watcher importer tests",
)
def test_watch_import_is_idempotent_review_only_and_append_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        eo_number = _unique_eo_number()
        _write_candidate(tmp_path, eo_number=eo_number)
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            first = await import_watch_inbox(factory, inbox=tmp_path)
            second = await import_watch_inbox(factory, inbox=tmp_path)

            assert first.imported == 1
            assert first.existing == 0
            assert second.imported == 0
            assert second.existing == 1

            async with factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT status, eo_number, source_url, candidate_sha256 "
                            "FROM legal_watch_discoveries WHERE eo_number=:eo_number"
                        ),
                        {"eo_number": eo_number},
                    )
                ).mappings().one()
                assert row["status"] == "REVIEW_REQUIRED"
                assert row["eo_number"] == eo_number
                assert row["source_url"].startswith("https://publication.pravo.gov.ru/")
                assert len(row["candidate_sha256"]) == 64

                with pytest.raises(DBAPIError, match="immutable"):
                    await session.execute(
                        text(
                            "UPDATE legal_watch_discoveries SET title='tampered' "
                            "WHERE eo_number=:eo_number"
                        ),
                        {"eo_number": eo_number},
                    )
                    await session.commit()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
