import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from legal_core.database import database_url
from legal_core.legal_watch_importer import import_watch_inbox
from legal_core.legal_watch_review import (
    LegalWatchReviewRequest,
    list_watch_discoveries,
    record_watch_review,
)
from legal_core.models import User
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _unique_eo_number() -> str:
    return f"98{uuid4().int % 10**16:016d}"


def _write_candidate(inbox: Path, *, eo_number: str) -> None:
    pdf = b"%PDF-1.7\nsynthetic watch review artifact"
    directory = inbox / eo_number
    directory.mkdir(parents=True)
    (directory / "official.pdf").write_bytes(pdf)
    metadata = {
        "schemaVersion": "dental-legal-watch.v1",
        "status": "REVIEW_REQUIRED",
        "autoPromotionAllowed": False,
        "eoNumber": eo_number,
        "title": "Synthetic reviewed discovery",
        "documentNumber": "review-test",
        "documentDate": "2026-08-30",
        "publicationDate": "2026-08-31",
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


def test_review_request_normalizes_reason_code_and_rejects_free_text() -> None:
    request = LegalWatchReviewRequest(
        reviewer_telegram_user_id=123,
        discovery_id=uuid4(),
        expected_candidate_sha256="a" * 64,
        decision="NEEDS_ANALYSIS",
        reason_code=" dental_scope_check ",
    )
    assert request.normalized_reason_code() == "DENTAL_SCOPE_CHECK"

    invalid = request.model_copy(update={"reason_code": "contains spaces"})
    with pytest.raises(ValueError, match="A-Z"):
        invalid.normalized_reason_code()


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL watcher review tests",
)
def test_legal_editor_review_is_sha_bound_idempotent_and_append_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        eo_number = _unique_eo_number()
        _write_candidate(tmp_path, eo_number=eo_number)
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            imported = await import_watch_inbox(factory, inbox=tmp_path)
            assert imported.imported == 1

            reviewer_telegram_id = int(f"87{uuid4().int % 10**8:08d}")
            async with factory() as session, session.begin():
                reviewer = User(
                    telegram_user_id=reviewer_telegram_id,
                    display_name="Synthetic watcher reviewer",
                    system_role="LEGAL_EDITOR",
                )
                session.add(reviewer)

            rows = await list_watch_discoveries(factory, limit=200, pending_only=True)
            discovery = next(row for row in rows if row.eo_number == eo_number)
            assert discovery.latest_decision is None

            request = LegalWatchReviewRequest(
                reviewer_telegram_user_id=reviewer_telegram_id,
                discovery_id=discovery.id,
                expected_candidate_sha256=discovery.candidate_sha256,
                decision="RELEVANT",
                reason_code="DENTAL_SCOPE_CONFIRMED",
            )
            first = await record_watch_review(factory, request)
            second = await record_watch_review(factory, request)
            assert first.created is True
            assert second.created is False
            assert first.event_id == second.event_id

            pending = await list_watch_discoveries(factory, limit=200, pending_only=True)
            assert all(row.id != discovery.id for row in pending)
            all_rows = await list_watch_discoveries(factory, limit=200, pending_only=False)
            reviewed = next(row for row in all_rows if row.id == discovery.id)
            assert reviewed.latest_decision == "RELEVANT"
            assert reviewed.latest_reason_code == "DENTAL_SCOPE_CONFIRMED"

            stale = request.model_copy(update={"expected_candidate_sha256": "b" * 64})
            with pytest.raises(ValueError, match="stale"):
                await record_watch_review(factory, stale)

            async with factory() as session:
                with pytest.raises(DBAPIError, match="immutable"):
                    await session.execute(
                        text(
                            "UPDATE legal_watch_review_events SET reason_code='TAMPERED' "
                            "WHERE id=:event_id"
                        ),
                        {"event_id": first.event_id},
                    )
                    await session.commit()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
