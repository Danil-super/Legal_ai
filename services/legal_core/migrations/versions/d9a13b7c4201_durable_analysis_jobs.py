"""Durable, tenant-scoped analysis jobs and bounded worker discovery."""

from alembic import op

revision = "d9a13b7c4201"
down_revision = "c8e5f1a2b6d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE analysis_jobs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL,
            case_id uuid NOT NULL,
            actor_membership_id uuid NOT NULL,
            message_id bigint NOT NULL CHECK (message_id > 0),
            state varchar(20) NOT NULL DEFAULT 'QUEUED'
                CHECK (state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
            lease_token uuid,
            lease_until timestamptz,
            error_code varchar(80),
            notified_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (clinic_id,case_id) REFERENCES cases(clinic_id,id) ON DELETE CASCADE,
            FOREIGN KEY (clinic_id,actor_membership_id) REFERENCES clinic_users(clinic_id,id),
            CHECK ((state='RUNNING') = (lease_token IS NOT NULL AND lease_until IS NOT NULL))
        );
        CREATE UNIQUE INDEX uq_analysis_jobs_active_case ON analysis_jobs(clinic_id,case_id)
            WHERE state IN ('QUEUED','RUNNING');
        CREATE INDEX ix_analysis_jobs_pending ON analysis_jobs(state,created_at);
        ALTER TABLE analysis_jobs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE analysis_jobs FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_analysis_jobs ON analysis_jobs
            USING (clinic_id = nullif(current_setting('app.current_clinic_id',true),'')::uuid)
            WITH CHECK (clinic_id = nullif(current_setting('app.current_clinic_id',true),'')::uuid);
    """)
    op.execute("""
        CREATE FUNCTION public.claim_analysis_job()
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
                    lease_token=gen_random_uuid(), lease_until=now()+interval '180 seconds',
                    updated_at=now()
                FROM candidate c WHERE j.id=c.id RETURNING j.*
            ) SELECT j.id,j.clinic_id,j.case_id,j.actor_membership_id,
                     u.telegram_user_id,j.lease_token
                FROM claimed j JOIN public.clinic_users cu ON cu.id=j.actor_membership_id
                JOIN public.users u ON u.id=cu.user_id;
        END; $$;
        REVOKE ALL ON FUNCTION public.claim_analysis_job() FROM PUBLIC;
        CREATE FUNCTION public.analysis_job_notifications()
        RETURNS TABLE(job_id uuid, telegram_user_id bigint, message_id bigint)
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
            SELECT j.id,u.telegram_user_id,j.message_id FROM public.analysis_jobs j
            JOIN public.clinic_users cu ON cu.id=j.actor_membership_id AND cu.clinic_id=j.clinic_id
            JOIN public.users u ON u.id=cu.user_id
            JOIN public.cases c ON c.id=j.case_id AND c.clinic_id=j.clinic_id
            WHERE j.state IN ('SUCCEEDED','FAILED') AND j.notified_at IS NULL
                AND cu.status='ACTIVE' AND u.status='ACTIVE' AND c.retention_due_at>now()
                AND EXISTS (SELECT 1 FROM public.subscription_entitlements se
                    WHERE se.clinic_id=j.clinic_id AND se.user_id=u.id AND se.status='ACTIVE'
                    AND se.starts_at<=now() AND (se.ends_at IS NULL OR se.ends_at>now()))
            ORDER BY j.updated_at,j.id LIMIT 20;
        $$;
        REVOKE ALL ON FUNCTION public.analysis_job_notifications() FROM PUBLIC;
    """)


def downgrade() -> None:
    # Jobs contain only operational references; refuse downgrade while a worker owns work.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM analysis_jobs WHERE state IN ('QUEUED','RUNNING')) THEN
            RAISE EXCEPTION 'drain analysis jobs before downgrade';
        END IF;
    END $$""")
    op.execute("DROP FUNCTION public.analysis_job_notifications()")
    op.execute("DROP FUNCTION public.claim_analysis_job()")
    op.drop_table("analysis_jobs")
