"""Keep assignment target separate from the true workflow actor.

Revision ID: e21b7c4d8a90
Revises: d10a6b3e7f91
"""

import sqlalchemy as sa
from alembic import op

revision = "e21b7c4d8a90"
down_revision = "d10a6b3e7f91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_escalation_workflow_events", sa.Column(
        "assigned_membership_id", sa.Uuid(), nullable=True,
    ))
    op.create_foreign_key(
        "fk_escalation_workflow_assignee", "case_escalation_workflow_events", "clinic_users",
        ["clinic_id", "assigned_membership_id"], ["clinic_id", "id"], ondelete="RESTRICT",
    )
    op.drop_constraint("case_escalation_workflow_events_action_check",
                       "case_escalation_workflow_events", type_="check")
    op.create_check_constraint(
        "ck_escalation_workflow_assignment", "case_escalation_workflow_events",
        "(action = 'ASSIGNED' AND assigned_membership_id IS NOT NULL) OR "
        "(action IN ('CLAIMED', 'RESOLVED') AND assigned_membership_id IS NULL)",
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM case_escalation_workflow_events WHERE action='ASSIGNED')"
    )):
        raise RuntimeError(
            "Keep assignment-compatible readers; assignment history cannot be erased"
        )
    op.drop_constraint("ck_escalation_workflow_assignment", "case_escalation_workflow_events",
                       type_="check")
    op.create_check_constraint("case_escalation_workflow_events_action_check",
                               "case_escalation_workflow_events",
                               "action IN ('CLAIMED', 'RESOLVED')")
    op.drop_constraint("fk_escalation_workflow_assignee", "case_escalation_workflow_events",
                       type_="foreignkey")
    op.drop_column("case_escalation_workflow_events", "assigned_membership_id")
