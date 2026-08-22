"""add audited legal approval

Revision ID: f4e21a8c9b37
Revises: c9a2e4f7b611
Create Date: 2026-08-22 17:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4e21a8c9b37"
down_revision: str | None = "c9a2e4f7b611"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM legal_versions WHERE approval_state = 'APPROVED') THEN
            RAISE EXCEPTION
              'preflight failed: legacy APPROVED legal versions require manual review migration';
          END IF;
        END $$
        """
    )
    op.add_column(
        "legal_versions",
        sa.Column(
            "artifact_kind",
            sa.String(length=30),
            server_default="NORMALIZED_EXCERPT",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_legal_versions_artifact_kind",
        "legal_versions",
        "artifact_kind IN ('NORMALIZED_EXCERPT', 'OFFICIAL_RAW')",
    )
    op.create_check_constraint(
        "ck_legal_versions_approved_official_raw",
        "legal_versions",
        "approval_state <> 'APPROVED' OR artifact_kind = 'OFFICIAL_RAW'",
    )
    op.create_check_constraint(
        "ck_legal_versions_raw_sha256",
        "legal_versions",
        "encode(digest(raw_bytes, 'sha256'), 'hex') = raw_sha256",
    )
    op.execute(
        "CREATE OR REPLACE VIEW production_legal_fragments AS "
        "SELECT f.id AS fragment_id, f.version_id, v.document_id, f.article, f.part, "
        "f.point, f.structural_path, f.fragment_text, f.text_sha256, v.effective_from, "
        "v.effective_to, v.source_url, v.raw_sha256, d.title AS document_title, "
        "d.issuer, d.official_number, v.version_date, v.publication_date "
        "FROM legal_fragments f "
        "JOIN legal_versions v ON v.id = f.version_id "
        "JOIN legal_sources s ON s.id = v.source_id "
        "JOIN legal_documents d ON d.id = v.document_id "
        "WHERE v.approval_state = 'APPROVED' AND v.artifact_kind = 'OFFICIAL_RAW' "
        "AND s.status = 'APPROVED'"
    )
    op.create_table(
        "legal_approval_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("legal_version_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("expected_sha256", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column(
            "checks_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("decision IN ('APPROVED', 'BLOCKED', 'REJECTED')"),
        sa.CheckConstraint("char_length(expected_sha256) = 64"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["legal_version_id"], ["legal_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_legal_approval_events_version_time",
        "legal_approval_events",
        ["legal_version_id", "created_at", "id"],
    )
    op.create_index(
        "uq_legal_approval_events_approved",
        "legal_approval_events",
        ["legal_version_id"],
        unique=True,
        postgresql_where=sa.text("decision = 'APPROVED'"),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_legal_approval_event_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'legal approval events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER legal_approval_events_append_only
        BEFORE UPDATE OR DELETE ON legal_approval_events
        FOR EACH ROW EXECUTE FUNCTION prevent_legal_approval_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER legal_approval_events_append_only ON legal_approval_events")
    op.execute("DROP FUNCTION prevent_legal_approval_event_mutation()")
    op.drop_index("uq_legal_approval_events_approved", table_name="legal_approval_events")
    op.drop_index("ix_legal_approval_events_version_time", table_name="legal_approval_events")
    op.drop_table("legal_approval_events")
    op.execute(
        "CREATE OR REPLACE VIEW production_legal_fragments AS "
        "SELECT f.id AS fragment_id, f.version_id, v.document_id, f.article, f.part, "
        "f.point, f.structural_path, f.fragment_text, f.text_sha256, v.effective_from, "
        "v.effective_to, v.source_url, v.raw_sha256, d.title AS document_title, "
        "d.issuer, d.official_number, v.version_date, v.publication_date "
        "FROM legal_fragments f "
        "JOIN legal_versions v ON v.id = f.version_id "
        "JOIN legal_sources s ON s.id = v.source_id "
        "JOIN legal_documents d ON d.id = v.document_id "
        "WHERE v.approval_state = 'APPROVED' AND s.status = 'APPROVED'"
    )
    op.drop_constraint("ck_legal_versions_raw_sha256", "legal_versions", type_="check")
    op.drop_constraint(
        "ck_legal_versions_approved_official_raw", "legal_versions", type_="check"
    )
    op.drop_constraint("ck_legal_versions_artifact_kind", "legal_versions", type_="check")
    op.drop_column("legal_versions", "artifact_kind")
