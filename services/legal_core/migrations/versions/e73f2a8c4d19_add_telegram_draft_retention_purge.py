"""add Telegram draft retention purge

Revision ID: e73f2a8c4d19
Revises: d4a6c1e9b273
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e73f2a8c4d19"
down_revision: str | None = "d4a6c1e9b273"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE FUNCTION public.purge_expired_telegram_intake_drafts() "
        "RETURNS integer LANGUAGE plpgsql SECURITY DEFINER "
        "SET search_path = pg_catalog, public AS $$ "
        "DECLARE deleted_count integer; "
        "BEGIN "
        "WITH deleted AS (DELETE FROM public.telegram_intake_drafts "
        "WHERE status IN ('DRAFT', 'ARCHIVED') "
        "AND purge_after <= timezone('utc', now()) RETURNING 1) "
        "SELECT count(*) INTO deleted_count FROM deleted; "
        "RETURN deleted_count; "
        "END; $$"
    )
    op.execute("REVOKE ALL ON FUNCTION public.purge_expired_telegram_intake_drafts() FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.purge_expired_telegram_intake_drafts()")
