"""Expand preparation storage without enabling report-free writers.

Revision ID: d10a6b3e7f91
Revises: c4a0c3f6e852
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "d10a6b3e7f91"
down_revision: str | None = "c4a0c3f6e852"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_EXPIRED_DRAFT_OWNERS = """
    PERFORM d.id FROM public.telegram_intake_drafts AS d
     WHERE d.status <> 'DRAFT' OR d.purge_after <= timezone('utc', now())
     ORDER BY d.id FOR UPDATE;
"""

_CLEAR_EXPIRED_PREPARATION = """
    UPDATE public.idempotency_records AS i
       SET response_json = NULL, lease_token = NULL, lease_until = NULL,
           state = CASE WHEN i.state = 'IN_PROGRESS' THEN 'FAILED' ELSE i.state END
     WHERE i.resource_type = 'INTAKE_PREPARATION'
       AND (i.response_json IS NOT NULL OR i.lease_token IS NOT NULL OR i.state = 'IN_PROGRESS')
       AND NOT EXISTS (
           SELECT 1 FROM public.telegram_intake_drafts AS d
            WHERE d.id = i.resource_id AND d.clinic_id = i.clinic_id
              AND d.actor_membership_id = i.actor_membership_id
              AND d.status = 'DRAFT' AND d.purge_after > timezone('utc', now())
       );
"""


def _draft_retention_function(expanded: bool) -> str:
    path = Path(__file__).with_name("c4a0c3f6e852_scrub_expired_draft_replies.py")
    spec = importlib.util.spec_from_file_location("preparation_predecessor_retention", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("predecessor draft retention function missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    definition = str(module._function(True))
    if not expanded:
        return definition
    if definition.count("BEGIN\n") != 1:
        raise RuntimeError("unexpected predecessor retention function")
    # Match the runtime draft→operation order, including a draft expiring during a wait.
    # Missing owners have no parent to lock and remain covered by the scrub below.
    return definition.replace(
        "BEGIN\n", "BEGIN\n" + _LOCK_EXPIRED_DRAFT_OWNERS + _CLEAR_EXPIRED_PREPARATION, 1,
    )


def upgrade() -> None:
    op.add_column("idempotency_records", sa.Column("lease_token", sa.Uuid(), nullable=True))
    op.add_column("idempotency_records", sa.Column(
        "lease_until", sa.DateTime(timezone=True), nullable=True,
    ))
    op.create_check_constraint("ck_idempotency_preparation_lease_pair", "idempotency_records",
                               "(lease_token IS NULL) = (lease_until IS NULL)")
    op.create_check_constraint("ck_idempotency_preparation_lease_scope", "idempotency_records",
                               "lease_token IS NULL OR (resource_type IS NOT NULL "
                               "AND resource_type = 'INTAKE_PREPARATION' "
                               "AND state = 'IN_PROGRESS')")
    op.add_column("telegram_case_workflows", sa.Column(
        "receipt_version", sa.Integer(), nullable=False, server_default="1",
    ))
    op.alter_column("telegram_case_workflows", "report_id", nullable=True)
    op.create_check_constraint("ck_telegram_workflow_receipt", "telegram_case_workflows",
                               "(receipt_version = 1 AND report_id IS NOT NULL) "
                               "OR (receipt_version = 2 AND report_id IS NULL)")
    op.add_column("analysis_jobs", sa.Column(
        "result_version", sa.Integer(), nullable=False, server_default="1",
    ))
    op.create_check_constraint("ck_analysis_job_result_version", "analysis_jobs",
                               "result_version IN (1, 2)")
    op.execute(_draft_retention_function(True))
    op.execute(_CLEAR_EXPIRED_PREPARATION)


def downgrade() -> None:
    # Never silently erase V2 receipts, result versions, leases or replay tombstones.
    # Fence in-flight writers BEFORE checking data, not only at each later ALTER.
    op.execute("LOCK TABLE public.analysis_jobs, public.idempotency_records, "
               "public.telegram_case_workflows IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.telegram_case_workflows WHERE receipt_version = 2)
               OR EXISTS (SELECT 1 FROM public.analysis_jobs WHERE result_version = 2)
               OR EXISTS (SELECT 1 FROM public.idempotency_records
                           WHERE resource_type = 'INTAKE_PREPARATION') THEN
                RAISE EXCEPTION 'cannot downgrade: preparation storage has retained V2 records';
            END IF;
        END $$;
    """)
    op.execute(_draft_retention_function(False))
    op.drop_constraint("ck_analysis_job_result_version", "analysis_jobs", type_="check")
    op.drop_column("analysis_jobs", "result_version")
    op.drop_constraint("ck_telegram_workflow_receipt", "telegram_case_workflows", type_="check")
    op.alter_column("telegram_case_workflows", "report_id", nullable=False)
    op.drop_column("telegram_case_workflows", "receipt_version")
    op.drop_constraint(
        "ck_idempotency_preparation_lease_scope", "idempotency_records", type_="check",
    )
    op.drop_constraint(
        "ck_idempotency_preparation_lease_pair", "idempotency_records", type_="check",
    )
    op.drop_column("idempotency_records", "lease_until")
    op.drop_column("idempotency_records", "lease_token")
