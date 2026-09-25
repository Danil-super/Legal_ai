"""add immutable legal review material inbox

Revision ID: a1b2c3d4e5f6
Revises: fc35d7e4a922
Create Date: 2026-09-25 18:35:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "fc35d7e4a922"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "legal_review_materials",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("package_key", sa.String(length=80), nullable=False),
        sa.Column("original_filename", sa.String(length=240), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column(
            "review_state",
            sa.String(length=30),
            server_default="METADATA_REQUIRED",
            nullable=False,
        ),
        sa.Column("source_name", sa.String(length=240), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_external_id", sa.String(length=120), nullable=True),
        sa.Column("raw_mime_type", sa.String(length=100), nullable=False),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("raw_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('LEGAL_COPY', 'CLINICAL_REFERENCE')"),
        sa.CheckConstraint("review_state = 'METADATA_REQUIRED'"),
        sa.CheckConstraint("encode(digest(raw_bytes, 'sha256'), 'hex') = raw_sha256"),
        sa.CheckConstraint("octet_length(raw_bytes) = raw_size_bytes"),
        sa.CheckConstraint("raw_size_bytes > 0 AND raw_size_bytes <= 50000000"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_key", "raw_sha256"),
    )
    op.create_index(
        "ix_legal_review_materials_queue",
        "legal_review_materials",
        ["package_key", "received_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_legal_review_materials_queue", table_name="legal_review_materials")
    op.drop_table("legal_review_materials")
