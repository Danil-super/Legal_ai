"""allow reviewed fragment revisions for the same immutable raw artifact

Revision ID: f19b4c6e7d20
Revises: e2f98a3c5d17
Create Date: 2026-08-22 16:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "f19b4c6e7d20"
down_revision = "e2f98a3c5d17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "legal_versions_document_id_raw_sha256_key",
        "legal_versions",
        type_="unique",
    )
    op.create_index(
        "uq_legal_versions_approved_raw",
        "legal_versions",
        ["document_id", "raw_sha256"],
        unique=True,
        postgresql_where=sa.text("approval_state = 'APPROVED'"),
    )


def downgrade() -> None:
    op.drop_index("uq_legal_versions_approved_raw", table_name="legal_versions")
    op.create_unique_constraint(
        "legal_versions_document_id_raw_sha256_key",
        "legal_versions",
        ["document_id", "raw_sha256"],
    )
