"""add immutable legal update review queue

Revision ID: a4d9b7c2e831
Revises: c6a4d8f15e72
Create Date: 2026-08-23 18:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d9b7c2e831"
down_revision: str | None = "c6a4d8f15e72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legal_update_review_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("previous_legal_version_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_legal_version_id", sa.Uuid(), nullable=False),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalized_sha256", sa.String(length=64), nullable=False),
        sa.Column("fragments_sha256", sa.String(length=64), nullable=False),
        sa.Column("structural_diff_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "structural_diff_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="REVIEW_REQUIRED", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("status = 'REVIEW_REQUIRED'"),
        sa.CheckConstraint("char_length(raw_sha256) = 64"),
        sa.CheckConstraint("char_length(normalized_sha256) = 64"),
        sa.CheckConstraint("char_length(fragments_sha256) = 64"),
        sa.CheckConstraint("char_length(structural_diff_sha256) = 64"),
        sa.CheckConstraint("char_length(candidate_sha256) = 64"),
        sa.ForeignKeyConstraint(["source_id"], ["legal_sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["legal_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["previous_legal_version_id"], ["legal_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_legal_version_id"], ["legal_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_sha256"),
    )
    op.create_index(
        "ix_legal_update_review_items_queue",
        "legal_update_review_items",
        ["document_id", "created_at", "id"],
    )
    op.execute(
        "CREATE FUNCTION legal_update_review_items_validate_insert() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE candidate_source uuid; candidate_document uuid; candidate_state text; "
        "candidate_raw text; candidate_normalized text; candidate_fragments text; "
        "prior_document uuid; "
        "BEGIN "
        "SELECT source_id, document_id, approval_state, raw_sha256, normalized_sha256, "
        "fragments_sha256 INTO candidate_source, candidate_document, candidate_state, "
        "candidate_raw, candidate_normalized, candidate_fragments "
        "FROM legal_versions WHERE id = NEW.candidate_legal_version_id FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'candidate legal version does not exist'; END IF; "
        "IF candidate_source <> NEW.source_id OR candidate_document <> NEW.document_id THEN "
        "RAISE EXCEPTION 'review item candidate identity mismatch'; END IF; "
        "IF candidate_state <> 'REVIEW_REQUIRED' THEN "
        "RAISE EXCEPTION 'review item candidate must require review'; END IF; "
        "IF candidate_raw <> NEW.raw_sha256 OR candidate_normalized <> NEW.normalized_sha256 "
        "OR candidate_fragments <> NEW.fragments_sha256 THEN "
        "RAISE EXCEPTION 'review item candidate checksum mismatch'; END IF; "
        "IF NOT EXISTS (SELECT 1 FROM legal_sources WHERE id = NEW.source_id "
        "AND status = 'APPROVED') THEN "
        "RAISE EXCEPTION 'review item source must be approved'; END IF; "
        "IF NEW.previous_legal_version_id IS NOT NULL THEN "
        "SELECT document_id INTO prior_document FROM legal_versions "
        "WHERE id = NEW.previous_legal_version_id FOR SHARE; "
        "IF NOT FOUND OR prior_document <> NEW.document_id THEN "
        "RAISE EXCEPTION 'review item previous version document mismatch'; END IF; "
        "END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER legal_update_review_items_validate_insert BEFORE INSERT "
        "ON legal_update_review_items FOR EACH ROW "
        "EXECUTE FUNCTION legal_update_review_items_validate_insert()"
    )
    op.execute(
        "CREATE TRIGGER legal_update_review_items_immutable BEFORE UPDATE OR DELETE "
        "ON legal_update_review_items FOR EACH ROW "
        "EXECUTE FUNCTION prevent_immutable_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS legal_update_review_items_immutable "
        "ON legal_update_review_items"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS legal_update_review_items_validate_insert "
        "ON legal_update_review_items"
    )
    op.execute("DROP FUNCTION IF EXISTS legal_update_review_items_validate_insert()")
    op.drop_table("legal_update_review_items")
