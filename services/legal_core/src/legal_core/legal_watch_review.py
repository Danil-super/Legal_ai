"""Human legal-editor review for immutable watcher discoveries.

This is intentionally a backend CLI, not a public HTTP endpoint. It lets an active LEGAL_EDITOR
list staged discoveries and append a classification decision bound to the exact candidate SHA-256.
It cannot create or approve LegalVersion rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.database import create_engine, create_session_factory

_REVIEW_DECISION = Literal["RELEVANT", "IRRELEVANT", "NEEDS_ANALYSIS"]
_REASON_CODE = re.compile(r"^[A-Z0-9_]{3,80}$")


class LegalWatchReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_telegram_user_id: int = Field(gt=0)
    discovery_id: UUID
    expected_candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: _REVIEW_DECISION
    reason_code: str = Field(min_length=3, max_length=80)

    def normalized_reason_code(self) -> str:
        value = self.reason_code.strip().upper()
        if _REASON_CODE.fullmatch(value) is None:
            raise ValueError("reason code must contain only A-Z, 0-9 and underscore")
        return value


@dataclass(frozen=True, slots=True)
class DiscoveryReviewRow:
    id: UUID
    eo_number: str
    title: str
    document_number: str | None
    publication_date: date
    candidate_sha256: str
    imported_at: datetime
    latest_decision: str | None
    latest_reason_code: str | None
    latest_reviewed_at: datetime | None


@dataclass(frozen=True, slots=True)
class RecordedReview:
    event_id: UUID
    created: bool


_LIST_DISCOVERIES = text(
    """
    SELECT d.id,
           d.eo_number,
           d.title,
           d.document_number,
           d.publication_date,
           d.candidate_sha256,
           d.imported_at,
           latest.decision AS latest_decision,
           latest.reason_code AS latest_reason_code,
           latest.created_at AS latest_reviewed_at
      FROM legal_watch_discoveries AS d
      LEFT JOIN LATERAL (
          SELECT e.decision, e.reason_code, e.created_at
            FROM legal_watch_review_events AS e
           WHERE e.discovery_id = d.id
           ORDER BY e.created_at DESC, e.id DESC
           LIMIT 1
      ) AS latest ON TRUE
     WHERE (:pending_only = false OR latest.decision IS NULL OR latest.decision = 'NEEDS_ANALYSIS')
     ORDER BY d.publication_date DESC, d.imported_at DESC, d.id DESC
     LIMIT :limit
    """
)

_SELECT_REVIEWER = text(
    """
    SELECT id
      FROM users
     WHERE telegram_user_id = :telegram_user_id
       AND status = 'ACTIVE'
       AND system_role = 'LEGAL_EDITOR'
    """
)

_SELECT_DISCOVERY = text(
    """
    SELECT id, candidate_sha256
      FROM legal_watch_discoveries
     WHERE id = :discovery_id
    """
)

_SELECT_EXACT_EVENT = text(
    """
    SELECT id
      FROM legal_watch_review_events
     WHERE discovery_id = :discovery_id
       AND actor_user_id = :actor_user_id
       AND decision = :decision
       AND reason_code = :reason_code
       AND expected_candidate_sha256 = :expected_candidate_sha256
    """
)

_INSERT_EVENT = text(
    """
    INSERT INTO legal_watch_review_events (
        discovery_id,
        actor_user_id,
        decision,
        reason_code,
        expected_candidate_sha256
    )
    VALUES (
        :discovery_id,
        :actor_user_id,
        :decision,
        :reason_code,
        :expected_candidate_sha256
    )
    ON CONFLICT (
        discovery_id,
        actor_user_id,
        decision,
        reason_code,
        expected_candidate_sha256
    ) DO NOTHING
    RETURNING id
    """
)


async def list_watch_discoveries(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    limit: int = 50,
    pending_only: bool = True,
) -> tuple[DiscoveryReviewRow, ...]:
    if not 1 <= limit <= 200:
        raise ValueError("review queue limit must be between 1 and 200")
    async with session_factory() as session:
        rows = (
            await session.execute(
                _LIST_DISCOVERIES,
                {"limit": limit, "pending_only": pending_only},
            )
        ).mappings()
        return tuple(DiscoveryReviewRow(**dict(row)) for row in rows)


async def record_watch_review(
    session_factory: async_sessionmaker[AsyncSession],
    request: LegalWatchReviewRequest,
) -> RecordedReview:
    reason_code = request.normalized_reason_code()
    async with session_factory() as session, session.begin():
        reviewer_id = await session.scalar(
            _SELECT_REVIEWER,
            {"telegram_user_id": request.reviewer_telegram_user_id},
        )
        if not isinstance(reviewer_id, UUID):
            raise PermissionError("active LEGAL_EDITOR role is required")

        discovery = (
            await session.execute(
                _SELECT_DISCOVERY,
                {"discovery_id": request.discovery_id},
            )
        ).mappings().one_or_none()
        if discovery is None:
            raise LookupError("watch discovery not found")
        if discovery["candidate_sha256"] != request.expected_candidate_sha256:
            raise ValueError("watch discovery changed or expected candidate SHA-256 is stale")

        parameters = {
            "discovery_id": request.discovery_id,
            "actor_user_id": reviewer_id,
            "decision": request.decision,
            "reason_code": reason_code,
            "expected_candidate_sha256": request.expected_candidate_sha256,
        }
        event_id = await session.scalar(_INSERT_EVENT, parameters)
        if isinstance(event_id, UUID):
            return RecordedReview(event_id=event_id, created=True)
        existing_id = await session.scalar(_SELECT_EXACT_EVENT, parameters)
        if not isinstance(existing_id, UUID):
            raise RuntimeError("review event conflict could not be resolved")
        return RecordedReview(event_id=existing_id, created=False)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review legal watcher discovery queue")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List watcher discoveries")
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--all", action="store_true")

    decide = subparsers.add_parser("decide", help="Append a human classification decision")
    decide.add_argument("--reviewer-telegram-id", type=int, required=True)
    decide.add_argument("--discovery-id", type=UUID, required=True)
    decide.add_argument("--expected-candidate-sha256", required=True)
    decide.add_argument(
        "--decision",
        choices=("RELEVANT", "IRRELEVANT", "NEEDS_ANALYSIS"),
        required=True,
    )
    decide.add_argument("--reason-code", required=True)
    return parser


async def _run_cli() -> None:
    args = _parser().parse_args()
    engine = create_engine()
    factory = create_session_factory(engine)
    try:
        if args.command == "list":
            rows = await list_watch_discoveries(
                factory,
                limit=args.limit,
                pending_only=not args.all,
            )
            for row in rows:
                print(
                    json.dumps(
                        {
                            "id": str(row.id),
                            "eoNumber": row.eo_number,
                            "title": row.title,
                            "documentNumber": row.document_number,
                            "publicationDate": row.publication_date.isoformat(),
                            "candidateSha256": row.candidate_sha256,
                            "latestDecision": row.latest_decision,
                            "latestReasonCode": row.latest_reason_code,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            return

        request = LegalWatchReviewRequest(
            reviewer_telegram_user_id=args.reviewer_telegram_id,
            discovery_id=args.discovery_id,
            expected_candidate_sha256=args.expected_candidate_sha256,
            decision=args.decision,
            reason_code=args.reason_code,
        )
        result = await record_watch_review(factory, request)
        print(
            json.dumps(
                {"eventId": str(result.event_id), "created": result.created},
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run_cli())


if __name__ == "__main__":
    main()
