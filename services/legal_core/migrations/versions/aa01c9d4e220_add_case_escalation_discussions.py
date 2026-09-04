"""add tenant-scoped discussion messages for required case escalations

Revision ID: aa01c9d4e220
Revises: e8f1c2a73b94
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "aa01c9d4e220"
down_revision: str | None = "e8f1c2a73b94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_case_escalations_tenant_id", "case_escalations", ["clinic_id", "id"]
    )
    op.create_table(
        "case_escalation_messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("escalation_id", sa.Uuid(), nullable=False),
        sa.Column("author_membership_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 1500"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "escalation_id"],
            ["case_escalations.clinic_id", "case_escalations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "author_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_case_escalation_messages_tenant_thread",
        "case_escalation_messages",
        ["clinic_id", "escalation_id", "id"],
    )
    op.execute("ALTER TABLE case_escalation_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE case_escalation_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_case_escalation_messages ON case_escalation_messages "
        "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
        "WITH CHECK (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE TRIGGER case_escalation_messages_immutable BEFORE UPDATE OR DELETE "
        "ON case_escalation_messages FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS case_escalation_messages_immutable ON case_escalation_messages"
    )
    op.drop_index("ix_case_escalation_messages_tenant_thread", table_name="case_escalation_messages")
    op.drop_table("case_escalation_messages")
    op.drop_constraint("uq_case_escalations_tenant_id", "case_escalations", type_="unique")
