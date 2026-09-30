"""Add immutable human review events for reference materials.

Revision ID: c4b5d6e7f809
Revises: b2c3d4e5f6a7
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4b5d6e7f809"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legal_reference_review_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("preparation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("group_key", sa.String(length=30), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("checks_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("raw_sha256 ~ '^[0-9a-f]{64}$'"),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'"),
        sa.ForeignKeyConstraint(
            ["preparation_id"], ["legal_material_preparations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("preparation_id"),
        sa.UniqueConstraint("actor_user_id", "idempotency_key"),
    )
    op.create_index(
        "ix_legal_reference_review_events_batch",
        "legal_reference_review_events",
        ["batch_id", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_legal_reference_review_event()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            prepared public.legal_material_preparations%ROWTYPE;
            editor_status text;
            editor_role text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'reference review events are append-only';
            END IF;
            SELECT * INTO prepared
            FROM public.legal_material_preparations
            WHERE id = NEW.preparation_id
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'reference preparation is missing';
            END IF;
            PERFORM 1 FROM public.legal_review_materials
            WHERE id = prepared.material_id
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'reference original is missing';
            END IF;
            SELECT status, system_role INTO editor_status, editor_role
            FROM public.users WHERE id = NEW.actor_user_id FOR SHARE;
            IF editor_status IS DISTINCT FROM 'ACTIVE'
               OR editor_role IS DISTINCT FROM 'LEGAL_EDITOR'
               OR prepared.kind NOT IN ('CLINICAL_REFERENCE', 'REFERENCE_FORM')
               OR prepared.raw_sha256 <> NEW.raw_sha256
               OR prepared.group_key <> NEW.group_key
               OR NEW.checks_json <> jsonb_build_object(
                    'originalReviewed', true,
                    'referenceOnlyUnderstood', true
               )
               OR EXISTS (
                    SELECT 1 FROM public.legal_material_preparations newer
                    WHERE newer.material_id = prepared.material_id
                      AND newer.revision > prepared.revision
               )
            THEN
                RAISE EXCEPTION 'reference review attestation is invalid or stale';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER legal_reference_review_events_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.legal_reference_review_events
        FOR EACH ROW EXECUTE FUNCTION public.guard_legal_reference_review_event();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS legal_reference_review_events_guard "
        "ON legal_reference_review_events"
    )
    op.execute("DROP FUNCTION IF EXISTS public.guard_legal_reference_review_event()")
    op.drop_index(
        "ix_legal_reference_review_events_batch", table_name="legal_reference_review_events"
    )
    op.drop_table("legal_reference_review_events")
