"""add tenant-scoped individual subscription entitlements

Revision ID: c413e2f8a901
Revises: f19b4c6e7d20
Create Date: 2026-08-22 23:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c413e2f8a901"
down_revision: str | None = "f19b4c6e7d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription_entitlements",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        sa.Column("plan_code", sa.String(length=80), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'CANCELLED')"),
        sa.CheckConstraint("ends_at IS NULL OR ends_at > starts_at"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "user_id"],
            ["clinic_users.clinic_id", "clinic_users.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "id"),
        sa.UniqueConstraint("clinic_id", "user_id"),
    )
    op.create_index(
        "ix_subscription_entitlements_access",
        "subscription_entitlements",
        ["user_id", "status", "starts_at"],
        unique=False,
    )
    op.create_table(
        "subscription_entitlement_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("entitlement_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("performed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "metadata_json",
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
        sa.CheckConstraint("event_type IN ('GRANTED', 'UPDATED', 'SUSPENDED', 'CANCELLED')"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "entitlement_id"],
            ["subscription_entitlements.clinic_id", "subscription_entitlements.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["performed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscription_entitlement_events_tenant_time",
        "subscription_entitlement_events",
        ["clinic_id", "created_at", "id"],
        unique=False,
    )

    for table_name in ("subscription_entitlements", "subscription_entitlement_events"):
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
            "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
            "WITH CHECK "
            "(clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
        )

    op.execute(
        "CREATE TRIGGER subscription_entitlement_events_immutable "
        "BEFORE UPDATE OR DELETE ON subscription_entitlement_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER subscription_entitlement_events_immutable "
        "ON subscription_entitlement_events"
    )
    op.drop_table("subscription_entitlement_events")
    op.drop_table("subscription_entitlements")
