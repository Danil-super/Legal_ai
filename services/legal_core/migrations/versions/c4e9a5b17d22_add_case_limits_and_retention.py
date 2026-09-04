"""add confirmed case limits and bounded case-content retention

Revision ID: c4e9a5b17d22
Revises: aa01c9d4e220
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e9a5b17d22"
down_revision: str | None = "aa01c9d4e220"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PURGE_CASE_CONTENT_SQL = """
CREATE FUNCTION public.purge_expired_case_content()
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    target record;
    retention_now timestamptz := timezone('utc', now());
    facts_count integer;
    reports_count integer;
    messages_count integer;
    purged_cases integer := 0;
BEGIN
    DELETE FROM public.audit_events
    WHERE created_at < retention_now - interval '12 months';
    DELETE FROM public.case_retention_events
    WHERE audit_purge_after <= retention_now;
    DELETE FROM public.cases
    WHERE content_purged_at <= retention_now - interval '12 months';

    FOR target IN
        SELECT id, clinic_id, case_no
        FROM public.cases
        WHERE retention_due_at <= retention_now
          AND content_purged_at IS NULL
        ORDER BY retention_due_at, id
        LIMIT 100
        FOR UPDATE SKIP LOCKED
    LOOP
        BEGIN
            EXECUTE 'ALTER TABLE public.case_facts DISABLE TRIGGER case_facts_immutable';
            EXECUTE 'ALTER TABLE public.case_reports DISABLE TRIGGER case_reports_immutable';
            EXECUTE 'ALTER TABLE public.telegram_case_workflows DISABLE TRIGGER telegram_case_workflows_immutable';
            EXECUTE 'ALTER TABLE public.case_escalation_messages DISABLE TRIGGER case_escalation_messages_immutable';
            EXECUTE 'ALTER TABLE public.case_analysis_claims DISABLE TRIGGER case_analysis_claims_immutable';
            EXECUTE 'ALTER TABLE public.case_analysis_runs DISABLE TRIGGER case_analysis_runs_immutable';
            EXECUTE 'ALTER TABLE public.case_escalations DISABLE TRIGGER case_escalations_immutable';
            EXECUTE 'ALTER TABLE public.case_risk_assessments DISABLE TRIGGER case_risk_assessments_immutable';
            DELETE FROM public.idempotency_records
            WHERE clinic_id = target.clinic_id
              AND (
                  resource_id = target.id
                  OR resource_id IN (
                      SELECT id FROM public.case_reports
                      WHERE clinic_id = target.clinic_id AND case_id = target.id
                  )
                  OR resource_id IN (
                      SELECT id FROM public.case_analysis_runs
                      WHERE clinic_id = target.clinic_id AND case_id = target.id
                  )
              );
            DELETE FROM public.case_analysis_claims
            WHERE clinic_id = target.clinic_id
              AND analysis_run_id IN (
                  SELECT id FROM public.case_analysis_runs
                  WHERE clinic_id = target.clinic_id AND case_id = target.id
              );
            DELETE FROM public.case_escalation_messages
            WHERE clinic_id = target.clinic_id
              AND escalation_id IN (
                  SELECT id FROM public.case_escalations
                  WHERE clinic_id = target.clinic_id AND case_id = target.id
              );
            GET DIAGNOSTICS messages_count = ROW_COUNT;
            DELETE FROM public.telegram_case_workflows
            WHERE clinic_id = target.clinic_id AND case_id = target.id;
            DELETE FROM public.case_reports
            WHERE clinic_id = target.clinic_id AND case_id = target.id;
            GET DIAGNOSTICS reports_count = ROW_COUNT;
            DELETE FROM public.case_facts
            WHERE clinic_id = target.clinic_id AND case_id = target.id;
            GET DIAGNOSTICS facts_count = ROW_COUNT;
            DELETE FROM public.case_escalations
            WHERE clinic_id = target.clinic_id AND case_id = target.id;
            DELETE FROM public.case_analysis_runs
            WHERE clinic_id = target.clinic_id AND case_id = target.id;
            DELETE FROM public.case_risk_assessments
            WHERE clinic_id = target.clinic_id AND case_id = target.id;
            UPDATE public.cases SET
                status = 'CONTENT_PURGED',
                primary_incident_type = NULL,
                incident_tags = '[]'::jsonb,
                title = NULL,
                service_date = NULL,
                incident_date = NULL,
                claim_date = NULL,
                retention_due_at = NULL,
                content_purged_at = retention_now,
                updated_at = retention_now,
                row_version = row_version + 1
            WHERE clinic_id = target.clinic_id AND id = target.id;
            INSERT INTO public.case_retention_events
                (
                    clinic_id,
                    case_id,
                    case_no,
                    facts_purged,
                    reports_purged,
                    discussion_messages_purged,
                    audit_purge_after
                )
            VALUES
                (
                    target.clinic_id,
                    target.id,
                    target.case_no,
                    facts_count,
                    reports_count,
                    messages_count,
                    retention_now + interval '12 months'
                );
        EXCEPTION WHEN OTHERS THEN
            EXECUTE 'ALTER TABLE public.case_facts ENABLE TRIGGER case_facts_immutable';
            EXECUTE 'ALTER TABLE public.case_reports ENABLE TRIGGER case_reports_immutable';
            EXECUTE 'ALTER TABLE public.telegram_case_workflows ENABLE TRIGGER telegram_case_workflows_immutable';
            EXECUTE 'ALTER TABLE public.case_escalation_messages ENABLE TRIGGER case_escalation_messages_immutable';
            EXECUTE 'ALTER TABLE public.case_analysis_claims ENABLE TRIGGER case_analysis_claims_immutable';
            EXECUTE 'ALTER TABLE public.case_analysis_runs ENABLE TRIGGER case_analysis_runs_immutable';
            EXECUTE 'ALTER TABLE public.case_escalations ENABLE TRIGGER case_escalations_immutable';
            EXECUTE 'ALTER TABLE public.case_risk_assessments ENABLE TRIGGER case_risk_assessments_immutable';
            RAISE;
        END;
        EXECUTE 'ALTER TABLE public.case_facts ENABLE TRIGGER case_facts_immutable';
        EXECUTE 'ALTER TABLE public.case_reports ENABLE TRIGGER case_reports_immutable';
        EXECUTE 'ALTER TABLE public.telegram_case_workflows ENABLE TRIGGER telegram_case_workflows_immutable';
        EXECUTE 'ALTER TABLE public.case_escalation_messages ENABLE TRIGGER case_escalation_messages_immutable';
        EXECUTE 'ALTER TABLE public.case_analysis_claims ENABLE TRIGGER case_analysis_claims_immutable';
        EXECUTE 'ALTER TABLE public.case_analysis_runs ENABLE TRIGGER case_analysis_runs_immutable';
        EXECUTE 'ALTER TABLE public.case_escalations ENABLE TRIGGER case_escalations_immutable';
        EXECUTE 'ALTER TABLE public.case_risk_assessments ENABLE TRIGGER case_risk_assessments_immutable';
        purged_cases := purged_cases + 1;
    END LOOP;
    RETURN purged_cases;
END;
$$
"""


def upgrade() -> None:
    op.add_column("cases", sa.Column("retention_due_at", sa.DateTime(timezone=True)))
    op.add_column("cases", sa.Column("content_purged_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_cases_retention_due",
        "cases",
        ["retention_due_at", "id"],
        postgresql_where=sa.text("retention_due_at IS NOT NULL AND content_purged_at IS NULL"),
    )
    op.create_index("ix_cases_content_purged_at", "cases", ["content_purged_at", "id"])
    op.create_table(
        "case_retention_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_no", sa.BigInteger(), nullable=False),
        sa.Column("facts_purged", sa.Integer(), nullable=False),
        sa.Column("reports_purged", sa.Integer(), nullable=False),
        sa.Column("discussion_messages_purged", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column("audit_purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_case_retention_events_tenant_time",
        "case_retention_events",
        ["clinic_id", "created_at", "id"],
    )
    op.create_index(
        "ix_case_retention_events_purge_after",
        "case_retention_events",
        ["audit_purge_after", "id"],
    )
    op.execute("ALTER TABLE case_retention_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE case_retention_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_case_retention_events ON case_retention_events "
        "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
        "WITH CHECK (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
    )
    op.execute(_PURGE_CASE_CONTENT_SQL)
    op.execute("REVOKE ALL ON FUNCTION public.purge_expired_case_content() FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.purge_expired_case_content()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_case_retention_events ON case_retention_events")
    op.drop_index("ix_case_retention_events_purge_after", table_name="case_retention_events")
    op.drop_index("ix_case_retention_events_tenant_time", table_name="case_retention_events")
    op.drop_table("case_retention_events")
    op.drop_index("ix_cases_content_purged_at", table_name="cases")
    op.drop_index("ix_cases_retention_due", table_name="cases")
    op.drop_column("cases", "content_purged_at")
    op.drop_column("cases", "retention_due_at")
