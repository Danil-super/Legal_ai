"""add versioned risk policy, immutable assessments and escalation records

Revision ID: f9e4d1c72a60
Revises: e2f98a3c5d17
Create Date: 2026-08-23 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9e4d1c72a60"
down_revision: str | Sequence[str] | None = ("c413e2f8a901", "e2f98a3c5d17")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_policy_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("policy_key", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="DRAFT", nullable=False),
        sa.Column("policy_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("status IN ('DRAFT', 'APPROVED', 'RETIRED')"),
        sa.CheckConstraint("char_length(content_sha256) = 64"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_key", "version"),
    )
    op.create_index(
        "uq_risk_policy_versions_one_approved",
        "risk_policy_versions",
        ["policy_key"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
    )
    op.create_table(
        "risk_policy_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("risk_policy_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("expected_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("decision IN ('APPROVED', 'RETIRED', 'BLOCKED')"),
        sa.CheckConstraint("char_length(expected_content_sha256) = 64"),
        sa.ForeignKeyConstraint(
            ["risk_policy_id"], ["risk_policy_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_risk_policy_events_policy_time",
        "risk_policy_events",
        ["risk_policy_id", "created_at", "id"],
    )
    op.create_table(
        "case_risk_assessments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("reason_codes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fact_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("evidence_trace_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "external_draft_allowed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', 'UNAVAILABLE')"),
        sa.CheckConstraint("char_length(fact_snapshot_sha256) = 64"),
        sa.CheckConstraint("char_length(evidence_trace_sha256) = 64"),
        sa.CheckConstraint("external_draft_allowed = false"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["policy_id"], ["risk_policy_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "id"),
    )
    op.create_index(
        "ix_case_risk_assessments_tenant_case",
        "case_risk_assessments",
        ["clinic_id", "case_id", "created_at"],
    )
    op.create_table(
        "case_escalations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_risk_assessment_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="REQUIRED", nullable=False),
        sa.Column("reason_codes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("level IN ('HIGH', 'CRITICAL')"),
        sa.CheckConstraint("status = 'REQUIRED'"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "case_risk_assessment_id"],
            ["case_risk_assessments.clinic_id", "case_risk_assessments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "case_risk_assessment_id"),
    )
    op.create_index(
        "ix_case_escalations_tenant_case",
        "case_escalations",
        ["clinic_id", "case_id", "created_at"],
    )

    for table_name in ("case_risk_assessments", "case_escalations"):
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
            "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
            "WITH CHECK "
            "(clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
        )

    op.execute(
        "CREATE FUNCTION risk_policy_events_validate_insert() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE policy_status text; policy_sha text; "
        "BEGIN "
        "SELECT status, content_sha256 INTO policy_status, policy_sha "
        "FROM risk_policy_versions WHERE id = NEW.risk_policy_id FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'risk policy does not exist'; END IF; "
        "IF NEW.expected_content_sha256 <> policy_sha THEN "
        "RAISE EXCEPTION 'risk policy event checksum mismatch'; END IF; "
        "IF NOT EXISTS (SELECT 1 FROM users WHERE id = NEW.actor_user_id "
        "AND status = 'ACTIVE' AND system_role = 'LEGAL_EDITOR') THEN "
        "RAISE EXCEPTION 'active LEGAL_EDITOR is required'; END IF; "
        "IF NEW.decision = 'APPROVED' AND policy_status <> 'DRAFT' THEN "
        "RAISE EXCEPTION 'only draft risk policy may be approved'; END IF; "
        "IF NEW.decision = 'RETIRED' AND policy_status <> 'APPROVED' THEN "
        "RAISE EXCEPTION 'only approved risk policy may be retired'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE FUNCTION risk_policy_versions_protect_content() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN "
        "IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'risk policy versions are append-only'; END IF; "
        "IF NEW.policy_key IS DISTINCT FROM OLD.policy_key "
        "OR NEW.version IS DISTINCT FROM OLD.version "
        "OR NEW.policy_json IS DISTINCT FROM OLD.policy_json OR "
        "NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 OR "
        "NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id OR "
        "NEW.created_at IS DISTINCT FROM OLD.created_at THEN "
        "RAISE EXCEPTION 'risk policy content is immutable'; END IF; "
        "IF OLD.status = 'DRAFT' AND NEW.status = 'APPROVED' "
        "AND NEW.approved_by_user_id IS NOT NULL AND NEW.approved_at IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM risk_policy_events WHERE risk_policy_id = OLD.id "
        "AND actor_user_id = NEW.approved_by_user_id AND decision = 'APPROVED' "
        "AND expected_content_sha256 = OLD.content_sha256) THEN RETURN NEW; END IF; "
        "IF OLD.status = 'APPROVED' AND NEW.status = 'RETIRED' "
        "AND EXISTS (SELECT 1 FROM risk_policy_events WHERE risk_policy_id = OLD.id "
        "AND decision = 'RETIRED' "
        "AND expected_content_sha256 = OLD.content_sha256) THEN RETURN NEW; END IF; "
        "RAISE EXCEPTION 'invalid risk policy status transition'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER risk_policy_events_validate_insert BEFORE INSERT ON risk_policy_events "
        "FOR EACH ROW EXECUTE FUNCTION risk_policy_events_validate_insert()"
    )
    op.execute(
        "CREATE TRIGGER risk_policy_events_immutable BEFORE UPDATE OR DELETE ON risk_policy_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
    )
    op.execute(
        "CREATE TRIGGER risk_policy_versions_protect_content BEFORE UPDATE OR DELETE "
        "ON risk_policy_versions FOR EACH ROW "
        "EXECUTE FUNCTION risk_policy_versions_protect_content()"
    )
    op.execute(
        "CREATE TRIGGER case_risk_assessments_immutable BEFORE UPDATE OR DELETE "
        "ON case_risk_assessments FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
    )
    op.execute(
        "CREATE TRIGGER case_escalations_immutable BEFORE UPDATE OR DELETE ON case_escalations "
        "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
    )


def downgrade() -> None:
    for table_name, trigger_name in (
        ("case_escalations", "case_escalations_immutable"),
        ("case_risk_assessments", "case_risk_assessments_immutable"),
        ("risk_policy_versions", "risk_policy_versions_protect_content"),
        ("risk_policy_events", "risk_policy_events_immutable"),
        ("risk_policy_events", "risk_policy_events_validate_insert"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS risk_policy_versions_protect_content()")
    op.execute("DROP FUNCTION IF EXISTS risk_policy_events_validate_insert()")
    op.drop_table("case_escalations")
    op.drop_table("case_risk_assessments")
    op.drop_table("risk_policy_events")
    op.drop_table("risk_policy_versions")
