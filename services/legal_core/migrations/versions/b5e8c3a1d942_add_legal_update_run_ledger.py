"""add immutable legal update run ledger

Revision ID: b5e8c3a1d942
Revises: a4d9b7c2e831
Create Date: 2026-08-23 18:35:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5e8c3a1d942"
down_revision: str | None = "a4d9b7c2e831"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE FUNCTION legal_update_run_result_sha256("
        "idempotency_digest text, run_status text, run_failure_code text, review_item uuid) "
        "RETURNS text LANGUAGE sql IMMUTABLE AS $$ "
        "SELECT encode(digest(idempotency_digest || '|' || run_status || '|' || "
        "coalesce(run_failure_code, '') || '|' || coalesce(review_item::text, ''), "
        "'sha256'), 'hex') "
        "$$"
    )
    op.create_table(
        "legal_update_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("review_item_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(idempotency_sha256) = 64"),
        sa.CheckConstraint("char_length(result_sha256) = 64"),
        sa.CheckConstraint(
            "result_sha256 = legal_update_run_result_sha256("
            "idempotency_sha256, status, failure_code, review_item_id)"
        ),
        sa.CheckConstraint(
            "(status = 'REVIEW_QUEUED' AND review_item_id IS NOT NULL AND failure_code IS NULL) "
            "OR (status = 'NO_CHANGE' AND review_item_id IS NULL AND failure_code IS NULL) "
            "OR (status IN ('FETCH_FAILED', 'PARSE_FAILED', 'VALIDATION_FAILED') "
            "AND review_item_id IS NULL AND failure_code IS NOT NULL)"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["legal_sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["legal_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["review_item_id"], ["legal_update_review_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_sha256"),
    )
    op.create_index(
        "ix_legal_update_runs_source_time",
        "legal_update_runs",
        ["source_id", "created_at", "id"],
    )
    op.execute(
        "CREATE FUNCTION legal_update_runs_validate_insert() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE item_source uuid; item_document uuid; "
        "BEGIN "
        "IF NEW.review_item_id IS NOT NULL THEN "
        "SELECT source_id, document_id INTO item_source, item_document "
        "FROM legal_update_review_items WHERE id = NEW.review_item_id FOR SHARE; "
        "IF NOT FOUND OR item_source <> NEW.source_id "
        "OR item_document IS DISTINCT FROM NEW.document_id THEN "
        "RAISE EXCEPTION 'update run review item identity mismatch'; END IF; "
        "END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER legal_update_runs_validate_insert BEFORE INSERT ON legal_update_runs "
        "FOR EACH ROW EXECUTE FUNCTION legal_update_runs_validate_insert()"
    )
    op.execute(
        "CREATE TRIGGER legal_update_runs_immutable BEFORE UPDATE OR DELETE ON legal_update_runs "
        "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS legal_update_runs_immutable ON legal_update_runs")
    op.execute(
        "DROP TRIGGER IF EXISTS legal_update_runs_validate_insert ON legal_update_runs"
    )
    op.execute("DROP FUNCTION IF EXISTS legal_update_runs_validate_insert()")
    op.drop_table("legal_update_runs")
    op.execute("DROP FUNCTION IF EXISTS legal_update_run_result_sha256(text, text, text, uuid)")
