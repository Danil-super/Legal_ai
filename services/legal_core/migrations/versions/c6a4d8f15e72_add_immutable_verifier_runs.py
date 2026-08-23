"""add immutable verifier run and claim evidence records

Revision ID: c6a4d8f15e72
Revises: f9e4d1c72a60
Create Date: 2026-08-23 17:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6a4d8f15e72"
down_revision: str | None = "f9e4d1c72a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_analysis_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_risk_assessment_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("fact_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("evidence_trace_sha256", sa.String(length=64), nullable=False),
        sa.Column("verifier_status", sa.String(length=20), nullable=False),
        sa.Column(
            "block_reason_codes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"), nullable=False,
        ),
        sa.CheckConstraint("verifier_status IN ('PASSED', 'BLOCKED')"),
        sa.CheckConstraint("char_length(fact_snapshot_sha256) = 64"),
        sa.CheckConstraint("char_length(evidence_trace_sha256) = 64"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "created_by_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "case_risk_assessment_id"],
            ["case_risk_assessments.clinic_id", "case_risk_assessments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "id"),
    )
    op.create_index(
        "ix_case_analysis_runs_tenant_case",
        "case_analysis_runs",
        ["clinic_id", "case_id", "created_at"],
    )
    op.create_table(
        "case_analysis_claims",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_run_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.String(length=80), nullable=False),
        sa.Column("claim_kind", sa.String(length=20), nullable=False),
        sa.Column("claim_sha256", sa.String(length=64), nullable=False),
        sa.Column("verification_result", sa.String(length=30), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column(
            "evidence_fragment_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"), nullable=False,
        ),
        sa.CheckConstraint("claim_kind IN ('LEGAL', 'ACTION')"),
        sa.CheckConstraint(
            "verification_result IN ('VERIFIED', 'UNSUPPORTED', 'CONTRADICTED', "
            "'NOT_APPLICABLE', 'INSUFFICIENT_FACTS')"
        ),
        sa.CheckConstraint("char_length(claim_sha256) = 64"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "analysis_run_id"],
            ["case_analysis_runs.clinic_id", "case_analysis_runs.id"], ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "analysis_run_id", "claim_id"),
    )
    op.create_index(
        "ix_case_analysis_claims_tenant_run",
        "case_analysis_claims",
        ["clinic_id", "analysis_run_id", "id"],
    )
    for table_name in ("case_analysis_runs", "case_analysis_claims"):
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
            "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
            "WITH CHECK (clinic_id = "
            "nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
        )
        op.execute(
            f'CREATE TRIGGER {table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} '
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    for table_name in ("case_analysis_claims", "case_analysis_runs"):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
    op.drop_table("case_analysis_claims")
    op.drop_table("case_analysis_runs")
