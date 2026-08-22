"""add durable Telegram case workflows

Revision ID: d81e5f3a921c
Revises: b7d4e91f2a60
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d81e5f3a921c"
down_revision: str | None = "b7d4e91f2a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_case_workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("actor_membership_id", sa.Uuid(), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(request_sha256) = 64"),
        sa.CheckConstraint("state = 'SUCCEEDED'"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "actor_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "case_id"],
            ["cases.clinic_id", "cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["report_id"], ["case_reports.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
        sa.UniqueConstraint("report_id"),
    )
    op.create_index(
        "ix_telegram_case_workflows_tenant",
        "telegram_case_workflows",
        ["clinic_id", "id"],
    )
    op.execute("ALTER TABLE telegram_case_workflows ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE telegram_case_workflows FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_telegram_case_workflows "
        "ON telegram_case_workflows "
        "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
        "WITH CHECK "
        "(clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE TRIGGER telegram_case_workflows_immutable "
        "BEFORE UPDATE OR DELETE ON telegram_case_workflows "
        "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS telegram_case_workflows_immutable "
        "ON telegram_case_workflows"
    )
    op.drop_index(
        "ix_telegram_case_workflows_tenant", table_name="telegram_case_workflows"
    )
    op.drop_table("telegram_case_workflows")
