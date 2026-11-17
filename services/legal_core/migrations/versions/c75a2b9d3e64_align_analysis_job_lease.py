"""Give the bounded two-pass case pipeline a coherent fixed worker lease."""

from alembic import op

revision = "c75a2b9d3e64"
down_revision = "b64f1a8c2d53"
branch_labels = None
depends_on = None

_CLAIM = """
    CREATE OR REPLACE FUNCTION public.claim_analysis_job()
    RETURNS TABLE(id uuid, clinic_id uuid, case_id uuid, actor_membership_id uuid,
                  telegram_user_id bigint, lease_token uuid)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
    BEGIN
        UPDATE public.analysis_jobs j SET state='FAILED', error_code='ANALYSIS_INTERRUPTED',
            lease_token=NULL, lease_until=NULL, updated_at=now()
            WHERE j.state='RUNNING' AND j.lease_until<now() AND j.attempts>=3;
        RETURN QUERY
        WITH candidate AS (
            SELECT j.id FROM public.analysis_jobs j JOIN public.cases c ON c.id=j.case_id
            WHERE (j.state='QUEUED' OR (j.state='RUNNING' AND j.lease_until<now()))
                AND j.attempts<3 AND c.retention_due_at>now()
            ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1
        ), claimed AS (
            UPDATE public.analysis_jobs j SET state='RUNNING', attempts=j.attempts+1,
                lease_token=gen_random_uuid(), lease_until=now()+interval '{lease_seconds} seconds',
                updated_at=now()
            FROM candidate c WHERE j.id=c.id RETURNING j.*
        ) SELECT j.id,j.clinic_id,j.case_id,j.actor_membership_id,
                 u.telegram_user_id,j.lease_token
            FROM claimed j JOIN public.clinic_users cu ON cu.id=j.actor_membership_id
            JOIN public.users u ON u.id=cu.user_id;
    END; $$;
"""


def upgrade() -> None:
    op.execute(_CLAIM.format(lease_seconds=300))


def downgrade() -> None:
    op.execute(_CLAIM.format(lease_seconds=180))
