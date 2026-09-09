"""add legal editor approval idempotency

Revision ID: d0e1f2a3b4c5
Revises: c4e9a5b17d22
Create Date: 2026-09-09 16:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c4e9a5b17d22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "legal_approval_events",
        sa.Column("idempotency_key", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "legal_approval_events",
        sa.Column("request_sha256", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_legal_approval_events_idempotency_pair",
        "legal_approval_events",
        "(idempotency_key IS NULL AND request_sha256 IS NULL) OR "
        "(idempotency_key IS NOT NULL AND char_length(request_sha256) = 64)",
    )
    op.create_index(
        "uq_legal_approval_events_editor_idempotency",
        "legal_approval_events",
        ["actor_user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_legal_approval_events_editor_idempotency",
        table_name="legal_approval_events",
    )
    op.drop_constraint(
        "ck_legal_approval_events_idempotency_pair",
        "legal_approval_events",
        type_="check",
    )
    op.drop_column("legal_approval_events", "request_sha256")
    op.drop_column("legal_approval_events", "idempotency_key")
