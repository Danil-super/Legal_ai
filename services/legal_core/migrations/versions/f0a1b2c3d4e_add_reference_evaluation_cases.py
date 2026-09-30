"""add de-identified reference evaluation workspace

Revision ID: f0a1b2c3d4e
Revises: e9f0a1b2c3d4
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e"
down_revision: str | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RETENTION_FUNCTIONS = """
CREATE FUNCTION public.claim_expired_reference_evaluation_versions()
RETURNS TABLE(version_id uuid, raw_object_key varchar, deletion_lease_token uuid)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    RETURN QUERY
    WITH target AS (
        SELECT version.id
        FROM public.reference_evaluation_case_versions AS version
        WHERE version.content_expires_at <= timezone('utc', now())
          AND version.content_purged_at IS NULL
          AND (
              version.deletion_claimed_at IS NULL
              OR version.deletion_claimed_at <= timezone('utc', now()) - interval '5 minutes'
          )
        ORDER BY version.content_expires_at, version.id
        LIMIT 100
        FOR UPDATE SKIP LOCKED
    ), claimed AS (
        UPDATE public.reference_evaluation_case_versions AS version
        SET deletion_claimed_at = timezone('utc', now()),
            deletion_lease_token = gen_random_uuid(),
            deletion_attempts = version.deletion_attempts + 1
        FROM target
        WHERE version.id = target.id
        RETURNING version.id, version.raw_object_key, version.deletion_lease_token
    )
    SELECT claimed.id, claimed.raw_object_key, claimed.deletion_lease_token FROM claimed;
END;
$$;

CREATE FUNCTION public.finalize_expired_reference_evaluation_version(
    version_id uuid,
    lease_token uuid
)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    UPDATE public.reference_evaluation_case_versions
    SET scenario_text = NULL,
        raw_mime_type = NULL,
        raw_size_bytes = NULL,
        raw_sha256 = NULL,
        raw_object_key = NULL,
        content_purged_at = timezone('utc', now()),
        deletion_claimed_at = NULL,
        deletion_lease_token = NULL
    WHERE id = version_id AND deletion_lease_token = lease_token;
    RETURN FOUND;
END;
$$;

CREATE FUNCTION public.release_expired_reference_evaluation_version(
    version_id uuid,
    lease_token uuid
)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    UPDATE public.reference_evaluation_case_versions
    SET deletion_claimed_at = NULL, deletion_lease_token = NULL
    WHERE id = version_id AND deletion_lease_token = lease_token;
    RETURN FOUND;
END;
$$;
"""

_REVIEW_GUARD = """
CREATE FUNCTION public.guard_reference_evaluation_review_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE creator uuid;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'reference evaluation review events are append-only';
    END IF;
    SELECT created_by_membership_id INTO creator
    FROM public.reference_evaluation_case_versions
    WHERE id = NEW.reference_version_id
      AND clinic_id = NEW.clinic_id
      AND reference_case_id = NEW.reference_case_id
    FOR SHARE;
    IF NOT FOUND OR creator = NEW.reviewer_membership_id THEN
        RAISE EXCEPTION 'reference evaluation review is invalid';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reference_evaluation_review_events_guard
BEFORE INSERT OR UPDATE OR DELETE ON public.reference_evaluation_review_events
FOR EACH ROW EXECUTE FUNCTION public.guard_reference_evaluation_review_event();
"""


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation_{table} ON {table} "
        "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
        "WITH CHECK (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "reference_evaluation_access_grants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("permission IN ('CONTRIBUTOR', 'REVIEWER')"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "id"),
        sa.UniqueConstraint("clinic_id", "membership_id", "permission"),
    )
    op.create_index(
        "ix_reference_evaluation_access_membership",
        "reference_evaluation_access_grants",
        ["clinic_id", "membership_id"],
    )
    op.create_table(
        "reference_evaluation_cases",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("case_no", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("created_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column("group_key", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="DRAFT", nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="1", nullable=False),
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
        sa.CheckConstraint(
            "status IN ('DRAFT', 'READY_FOR_REVIEW', 'CHANGES_REQUIRED', "
            "'APPROVED_FOR_EVALUATION', 'REJECTED', 'RETIRED')"
        ),
        sa.CheckConstraint(
            "group_key IN ('clinical', 'labour', 'courts', 'privacy', 'licensing', "
            "'healthcare', 'general')"
        ),
        sa.CheckConstraint("current_version > 0"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "created_by_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "id"),
        sa.UniqueConstraint("case_no"),
    )
    op.create_index(
        "ix_reference_evaluation_cases_tenant",
        "reference_evaluation_cases",
        ["clinic_id", "updated_at", "id"],
    )
    op.create_table(
        "reference_evaluation_case_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("reference_case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("expected_route", sa.String(length=30), nullable=False),
        sa.Column("scenario_text", sa.Text(), nullable=True),
        sa.Column("scenario_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_mime_type", sa.String(length=100), nullable=True),
        sa.Column("raw_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("raw_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_object_key", sa.String(length=512), nullable=True),
        sa.Column("content_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_lease_token", sa.Uuid(), nullable=True),
        sa.Column("deletion_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("expected_route IN ('ABSTAIN', 'HUMAN_ESCALATION', 'INTERNAL_DRAFT')"),
        sa.CheckConstraint("char_length(scenario_sha256) = 64"),
        sa.CheckConstraint(
            "(raw_object_key IS NULL AND raw_mime_type IS NULL AND raw_size_bytes IS NULL "
            "AND raw_sha256 IS NULL) OR "
            "(raw_object_key IS NOT NULL AND raw_mime_type IS NOT NULL "
            "AND raw_size_bytes > 0 AND char_length(raw_sha256) = 64)"
        ),
        sa.CheckConstraint("deletion_attempts >= 0"),
        sa.ForeignKeyConstraint(
            ["clinic_id", "reference_case_id"],
            ["reference_evaluation_cases.clinic_id", "reference_evaluation_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "created_by_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "id"),
        sa.UniqueConstraint("clinic_id", "reference_case_id", "version"),
        sa.UniqueConstraint("raw_object_key"),
    )
    op.create_index(
        "ix_reference_evaluation_versions_expiry",
        "reference_evaluation_case_versions",
        ["content_expires_at", "id"],
    )
    op.create_table(
        "reference_evaluation_review_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("reference_case_id", sa.Uuid(), nullable=False),
        sa.Column("reference_version_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_membership_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVE_FOR_EVALUATION', 'CHANGES_REQUIRED', 'REJECT', 'RETIRE')"
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "reference_case_id"],
            ["reference_evaluation_cases.clinic_id", "reference_evaluation_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "reference_version_id"],
            [
                "reference_evaluation_case_versions.clinic_id",
                "reference_evaluation_case_versions.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id", "reviewer_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "id"),
        sa.UniqueConstraint("clinic_id", "reference_case_id", "reference_version_id", "decision"),
    )
    op.create_index(
        "ix_reference_evaluation_review_case",
        "reference_evaluation_review_events",
        ["clinic_id", "reference_case_id", "created_at"],
    )
    for table in (
        "reference_evaluation_access_grants",
        "reference_evaluation_cases",
        "reference_evaluation_case_versions",
        "reference_evaluation_review_events",
    ):
        _enable_tenant_rls(table)
    op.execute(_REVIEW_GUARD)
    op.execute(_RETENTION_FUNCTIONS)
    for function in (
        "guard_reference_evaluation_review_event()",
        "claim_expired_reference_evaluation_versions()",
        "finalize_expired_reference_evaluation_version(uuid,uuid)",
        "release_expired_reference_evaluation_version(uuid,uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS reference_evaluation_review_events_guard "
        "ON reference_evaluation_review_events"
    )
    for function in (
        "release_expired_reference_evaluation_version(uuid,uuid)",
        "finalize_expired_reference_evaluation_version(uuid,uuid)",
        "claim_expired_reference_evaluation_versions()",
        "guard_reference_evaluation_review_event()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function}")
    for table, index in (
        ("reference_evaluation_review_events", "ix_reference_evaluation_review_case"),
        ("reference_evaluation_case_versions", "ix_reference_evaluation_versions_expiry"),
        ("reference_evaluation_cases", "ix_reference_evaluation_cases_tenant"),
        ("reference_evaluation_access_grants", "ix_reference_evaluation_access_membership"),
    ):
        op.drop_index(index, table_name=table)
    for table in (
        "reference_evaluation_review_events",
        "reference_evaluation_case_versions",
        "reference_evaluation_cases",
        "reference_evaluation_access_grants",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.drop_table(table)
