"""bind durable Telegram workflow reports to their tenant and case

Revision ID: e2f98a3c5d17
Revises: d81e5f3a921c
Create Date: 2026-08-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e2f98a3c5d17"
down_revision: str | None = "d81e5f3a921c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_case_reports_clinic_case_id",
        "case_reports",
        ["clinic_id", "case_id", "id"],
    )
    op.drop_constraint(
        "telegram_case_workflows_report_id_fkey",
        "telegram_case_workflows",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_telegram_case_workflows_report_case",
        "telegram_case_workflows",
        "case_reports",
        ["clinic_id", "case_id", "report_id"],
        ["clinic_id", "case_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_telegram_case_workflows_report_case",
        "telegram_case_workflows",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "telegram_case_workflows_report_id_fkey",
        "telegram_case_workflows",
        "case_reports",
        ["report_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("uq_case_reports_clinic_case_id", "case_reports", type_="unique")
