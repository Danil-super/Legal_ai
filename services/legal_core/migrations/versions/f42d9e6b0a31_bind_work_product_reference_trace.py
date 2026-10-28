"""Bind the existing optional reference trace column to immutable source content.

Revision ID: f42d9e6b0a31
Revises: f31c8d5a9e20
"""

from alembic import op

revision = "f42d9e6b0a31"
down_revision = "f31c8d5a9e20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_work_product_reference_trace", "case_work_product_versions",
        "payload_json#>>'{source,context,referenceExampleTraceSha256}' "
        "IS NOT DISTINCT FROM reference_example_trace_sha256",
    )


def downgrade() -> None:
    # Old strict readers cannot interpret retained analogy-bound sources. Fence
    # writers, expose owner-hidden rows only inside this owner DDL transaction,
    # and refuse rollback while such history exists. Refusal rolls FORCE back.
    op.execute("LOCK TABLE public.case_work_product_versions IN ACCESS EXCLUSIVE MODE")
    op.execute("ALTER TABLE public.case_work_product_versions NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.case_work_product_versions
                       WHERE reference_example_trace_sha256 IS NOT NULL) THEN
                RAISE EXCEPTION 'cannot downgrade: reference-bound history is retained';
            END IF;
        END $$;
    """)
    op.execute("ALTER TABLE public.case_work_product_versions FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_work_product_reference_trace", "case_work_product_versions",
                       type_="check")
