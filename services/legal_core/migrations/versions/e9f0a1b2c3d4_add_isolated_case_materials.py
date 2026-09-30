"""add isolated short-lived anonymised case materials

Revision ID: e9f0a1b2c3d4
Revises: d6e7f8a9b0c1
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DRAFT_PURGE_SQL = """
CREATE OR REPLACE FUNCTION public.purge_expired_telegram_intake_drafts()
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE deleted_count integer;
BEGIN
    WITH deleted AS (
        DELETE FROM public.telegram_intake_drafts AS draft
        WHERE draft.status IN ('DRAFT', 'ARCHIVED')
          AND draft.purge_after <= timezone('utc', now())
          AND NOT EXISTS (
              SELECT 1 FROM public.case_materials AS material
              WHERE material.clinic_id = draft.clinic_id AND material.draft_id = draft.id
          )
        RETURNING 1
    )
    SELECT count(*) INTO deleted_count FROM deleted;
    RETURN deleted_count;
END;
$$
"""

_OLD_DRAFT_PURGE_SQL = """
CREATE OR REPLACE FUNCTION public.purge_expired_telegram_intake_drafts()
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE deleted_count integer;
BEGIN
    WITH deleted AS (
        DELETE FROM public.telegram_intake_drafts
        WHERE status IN ('DRAFT', 'ARCHIVED')
          AND purge_after <= timezone('utc', now())
        RETURNING 1
    )
    SELECT count(*) INTO deleted_count FROM deleted;
    RETURN deleted_count;
END;
$$
"""

_CASE_PURGE_ANCHOR = """WHERE retention_due_at <= retention_now
          AND content_purged_at IS NULL"""
_CASE_PURGE_GATED = """WHERE retention_due_at <= retention_now
          AND content_purged_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM public.case_materials AS material
              WHERE material.clinic_id = cases.clinic_id AND material.case_id = cases.id
          )"""

_MATERIAL_RETENTION_FUNCTIONS = """
CREATE FUNCTION public.claim_expired_case_materials()
RETURNS TABLE(material_id uuid, raw_object_key varchar, deletion_lease_token uuid)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    RETURN QUERY
    WITH target AS (
        SELECT material.id
        FROM public.case_materials AS material
        WHERE material.expires_at <= timezone('utc', now())
          AND (
              material.deletion_claimed_at IS NULL
              OR material.deletion_claimed_at <= timezone('utc', now()) - interval '5 minutes'
          )
        ORDER BY material.expires_at, material.id
        LIMIT 100
        FOR UPDATE SKIP LOCKED
    ), claimed AS (
        UPDATE public.case_materials AS material
        SET deletion_claimed_at = timezone('utc', now()),
            deletion_lease_token = gen_random_uuid(),
            deletion_attempts = material.deletion_attempts + 1
        FROM target
        WHERE material.id = target.id
        RETURNING material.id, material.raw_object_key, material.deletion_lease_token
    )
    SELECT claimed.id, claimed.raw_object_key, claimed.deletion_lease_token FROM claimed;
END;
$$;

CREATE FUNCTION public.finalize_expired_case_material_deletion(
    material_id uuid,
    lease_token uuid
)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    DELETE FROM public.case_materials
    WHERE id = material_id AND deletion_lease_token = lease_token;
    RETURN FOUND;
END;
$$;

CREATE FUNCTION public.release_expired_case_material_deletion(
    material_id uuid,
    lease_token uuid
)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    UPDATE public.case_materials
    SET deletion_claimed_at = NULL, deletion_lease_token = NULL
    WHERE id = material_id AND deletion_lease_token = lease_token;
    RETURN FOUND;
END;
$$;
"""


def _replace_case_purge(*, upgrade: bool) -> None:
    definition = str(
        op.get_bind().scalar(
            sa.text(
                "SELECT pg_get_functiondef("
                "'public.purge_expired_case_content()'::regprocedure)"
            )
        )
    )
    source, target = (
        (_CASE_PURGE_ANCHOR, _CASE_PURGE_GATED)
        if upgrade
        else (_CASE_PURGE_GATED, _CASE_PURGE_ANCHOR)
    )
    if definition.count(source) != 1:
        raise RuntimeError("Unexpected retention function: cannot safely gate case materials")
    op.execute(sa.text(definition.replace(source, target)))


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_telegram_intake_drafts_tenant_id", "telegram_intake_drafts", ["clinic_id", "id"]
    )
    op.create_table(
        "case_materials",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=True),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("uploader_membership_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("raw_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_object_key", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletion_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_lease_token", sa.Uuid(), nullable=True),
        sa.Column("deletion_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("num_nonnulls(draft_id, case_id) = 1"),
        sa.CheckConstraint("char_length(raw_sha256) = 64"),
        sa.CheckConstraint("raw_size_bytes > 0"),
        sa.CheckConstraint("deletion_attempts >= 0"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "draft_id"],
            ["telegram_intake_drafts.clinic_id", "telegram_intake_drafts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "uploader_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "id"),
        sa.UniqueConstraint("raw_object_key"),
        sa.UniqueConstraint("clinic_id", "draft_id", "raw_sha256"),
    )
    op.create_index("ix_case_materials_draft", "case_materials", ["clinic_id", "draft_id", "id"])
    op.create_index("ix_case_materials_case", "case_materials", ["clinic_id", "case_id", "id"])
    op.create_index("ix_case_materials_expiry", "case_materials", ["expires_at", "id"])
    op.execute("ALTER TABLE case_materials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE case_materials FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_case_materials ON case_materials "
        "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
        "WITH CHECK (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
    )
    op.execute(_DRAFT_PURGE_SQL)
    _replace_case_purge(upgrade=True)
    op.execute(_MATERIAL_RETENTION_FUNCTIONS)
    op.execute("REVOKE ALL ON FUNCTION public.purge_expired_telegram_intake_drafts() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.purge_expired_case_content() FROM PUBLIC")
    for function in (
        "claim_expired_case_materials()",
        "finalize_expired_case_material_deletion(uuid,uuid)",
        "release_expired_case_material_deletion(uuid,uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")


def downgrade() -> None:
    _replace_case_purge(upgrade=False)
    op.execute(_OLD_DRAFT_PURGE_SQL)
    op.execute("DROP FUNCTION IF EXISTS public.release_expired_case_material_deletion(uuid,uuid)")
    op.execute("DROP FUNCTION IF EXISTS public.finalize_expired_case_material_deletion(uuid,uuid)")
    op.execute("DROP FUNCTION IF EXISTS public.claim_expired_case_materials()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_case_materials ON case_materials")
    op.drop_index("ix_case_materials_expiry", table_name="case_materials")
    op.drop_index("ix_case_materials_case", table_name="case_materials")
    op.drop_index("ix_case_materials_draft", table_name="case_materials")
    op.drop_table("case_materials")
    op.drop_constraint(
        "uq_telegram_intake_drafts_tenant_id", "telegram_intake_drafts", type_="unique"
    )
