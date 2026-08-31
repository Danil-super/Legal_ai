"""add durable Telegram intake drafts

Revision ID: d4a6c1e9b273
Revises: b5e8c3a1d942
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4a6c1e9b273"
down_revision: str | None = "b5e8c3a1d942"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_intake_drafts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("actor_membership_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="DRAFT", nullable=False),
        sa.Column("wizard_state", sa.String(length=40), nullable=False),
        sa.Column(
            "draft_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT', 'ARCHIVED', 'SUBMITTED')"),
        sa.CheckConstraint("revision > 0"),
        sa.CheckConstraint("jsonb_typeof(draft_json) = 'object'"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "actor_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_telegram_intake_drafts_actor_active",
        "telegram_intake_drafts",
        ["clinic_id", "actor_membership_id", "status", "updated_at", "id"],
    )
    op.execute("ALTER TABLE telegram_intake_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE telegram_intake_drafts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_telegram_intake_drafts "
        "ON telegram_intake_drafts "
        "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
        "WITH CHECK "
        "(clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_intake_drafts_actor_active", table_name="telegram_intake_drafts")
    op.drop_table("telegram_intake_drafts")
