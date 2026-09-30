"""Run the reference-review guard with the owner-only lock capability.

Revision ID: d6e7f8a9b0c1
Revises: c4b5d6e7f809
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "c4b5d6e7f809"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.guard_legal_reference_review_event()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
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
        REVOKE ALL ON FUNCTION public.guard_legal_reference_review_event() FROM PUBLIC;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER FUNCTION public.guard_legal_reference_review_event() SECURITY INVOKER;
        ALTER FUNCTION public.guard_legal_reference_review_event() RESET search_path;
        GRANT EXECUTE ON FUNCTION public.guard_legal_reference_review_event() TO PUBLIC;
        """
    )
