"""Keep old case files storage-only; record explicit analysis consent separately."""

import sqlalchemy as sa
from alembic import op

revision = "a53e0f7b1c42"
down_revision = "f42d9e6b0a31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_materials", sa.Column(
        "analysis_use_allowed", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM case_materials WHERE analysis_use_allowed) THEN
            RAISE EXCEPTION 'cannot drop retained case material analysis consent';
          END IF;
        END $$;
    """)
    op.drop_column("case_materials", "analysis_use_allowed")
