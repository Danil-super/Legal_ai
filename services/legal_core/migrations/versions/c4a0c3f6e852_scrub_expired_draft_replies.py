"""Erase expired draft payloads without erasing replay prevention metadata.

Revision ID: c4a0c3f6e852
Revises: c3f9b2e5d741
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4a0c3f6e852"
down_revision: str | None = "c3f9b2e5d741"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _function(clear_replies: bool) -> str:
    returning = "id, clinic_id, actor_membership_id" if clear_replies else "1"
    clear = """
      , cleared AS (
        UPDATE public.idempotency_records i SET response_json = NULL
          FROM deleted d
         WHERE i.resource_type = 'TELEGRAM_INTAKE_DRAFT'
           AND i.resource_id = d.id AND i.clinic_id = d.clinic_id
           AND i.actor_membership_id = d.actor_membership_id
        RETURNING i.id
      )
    """ if clear_replies else ""
    return f"""
      CREATE OR REPLACE FUNCTION public.purge_expired_telegram_intake_drafts()
      RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, public AS $$
      DECLARE deleted_count integer;
      BEGIN
        WITH deleted AS (
          DELETE FROM public.telegram_intake_drafts
           WHERE status IN ('DRAFT','ARCHIVED') AND purge_after <= timezone('utc',now())
           RETURNING {returning}
        ) {clear}
        SELECT count(*) INTO deleted_count FROM deleted;
        RETURN deleted_count;
      END; $$
    """


def upgrade() -> None:
    op.execute(_function(True))
    # Backfill only payloads whose matching draft row was already removed; do not
    # erase active/archived/submitted rows, receipts, hashes, scope or actor ownership.
    op.execute("""
      UPDATE public.idempotency_records i SET response_json = NULL
       WHERE i.resource_type = 'TELEGRAM_INTAKE_DRAFT' AND i.response_json IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM public.telegram_intake_drafts d
           WHERE d.id = i.resource_id AND d.clinic_id = i.clinic_id
             AND d.actor_membership_id = i.actor_membership_id)
    """)


def downgrade() -> None:
    # TTL-expired private text intentionally cannot be recovered by rollback.
    # Existing function ownership/execute ACL remains unchanged in both directions.
    op.execute(_function(False))
