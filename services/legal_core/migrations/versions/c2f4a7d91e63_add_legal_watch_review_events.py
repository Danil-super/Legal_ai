"""add append-only human review decisions for watcher discoveries

Revision ID: c2f4a7d91e63
Revises: b8d7c9e12f30
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c2f4a7d91e63"
down_revision: str | None = "b8d7c9e12f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE legal_watch_review_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            discovery_id uuid NOT NULL,
            actor_user_id uuid NOT NULL,
            decision varchar(30) NOT NULL,
            reason_code varchar(80) NOT NULL,
            expected_candidate_sha256 varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
            CONSTRAINT fk_legal_watch_review_events_discovery
                FOREIGN KEY (discovery_id)
                REFERENCES legal_watch_discoveries(id) ON DELETE RESTRICT,
            CONSTRAINT fk_legal_watch_review_events_actor
                FOREIGN KEY (actor_user_id)
                REFERENCES users(id) ON DELETE RESTRICT,
            CONSTRAINT ck_legal_watch_review_events_decision
                CHECK (decision IN ('RELEVANT', 'IRRELEVANT', 'NEEDS_ANALYSIS')),
            CONSTRAINT ck_legal_watch_review_events_reason
                CHECK (reason_code ~ '^[A-Z0-9_]{3,80}$'),
            CONSTRAINT ck_legal_watch_review_events_candidate_sha
                CHECK (expected_candidate_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT uq_legal_watch_review_events_exact
                UNIQUE (
                    discovery_id,
                    actor_user_id,
                    decision,
                    reason_code,
                    expected_candidate_sha256
                )
        )
        """
    )
    op.create_index(
        "ix_legal_watch_review_events_discovery_time",
        "legal_watch_review_events",
        ["discovery_id", "created_at", "id"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER legal_watch_review_events_append_only
        BEFORE UPDATE OR DELETE ON legal_watch_review_events
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS legal_watch_review_events_append_only "
        "ON legal_watch_review_events"
    )
    op.drop_index(
        "ix_legal_watch_review_events_discovery_time",
        table_name="legal_watch_review_events",
    )
    op.drop_table("legal_watch_review_events")
