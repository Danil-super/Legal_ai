"""Append-only lawyer assignment/resolution and chronological discussion index."""

import sqlalchemy as sa
from alembic import op

revision = "fa93c5b2d710"
down_revision = "c8e5f1a2b6d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_escalation_workflow_events",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(), nullable=False, unique=True),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("escalation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_membership_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("action IN ('CLAIMED', 'RESOLVED')"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "escalation_id"],
            ["case_escalations.clinic_id", "case_escalations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "actor_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_escalation_workflow_thread",
        "case_escalation_workflow_events",
        ["clinic_id", "escalation_id", "sequence"],
    )
    op.create_index(
        "ix_escalation_discussion_chronology",
        "case_escalation_messages",
        ["clinic_id", "escalation_id", "created_at", "id"],
    )
    op.execute("ALTER TABLE case_escalation_workflow_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE case_escalation_workflow_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_escalation_workflow ON case_escalation_workflow_events "
        "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
        "WITH CHECK (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE TRIGGER escalation_workflow_immutable BEFORE UPDATE OR DELETE "
        "ON case_escalation_workflow_events FOR EACH ROW "
        "EXECUTE FUNCTION prevent_immutable_mutation()"
    )


def downgrade() -> None:
    op.drop_index("ix_escalation_discussion_chronology", table_name="case_escalation_messages")
    op.drop_table("case_escalation_workflow_events")
