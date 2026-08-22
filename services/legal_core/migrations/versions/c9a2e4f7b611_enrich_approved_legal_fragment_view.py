"""enrich approved legal fragment view

Revision ID: c9a2e4f7b611
Revises: 8b1773dcd131
Create Date: 2026-08-22 16:40:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9a2e4f7b611"
down_revision: str | None = "8b1773dcd131"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE OR REPLACE VIEW production_legal_fragments AS "
        "SELECT f.id AS fragment_id, f.version_id, v.document_id, f.article, f.part, "
        "f.point, f.structural_path, f.fragment_text, f.text_sha256, v.effective_from, "
        "v.effective_to, v.source_url, v.raw_sha256, d.title AS document_title, "
        "d.issuer, d.official_number, v.version_date, v.publication_date "
        "FROM legal_fragments f "
        "JOIN legal_versions v ON v.id = f.version_id "
        "JOIN legal_sources s ON s.id = v.source_id "
        "JOIN legal_documents d ON d.id = v.document_id "
        "WHERE v.approval_state = 'APPROVED' AND s.status = 'APPROVED'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW production_legal_fragments")
    op.execute(
        "CREATE VIEW production_legal_fragments AS "
        "SELECT f.id AS fragment_id, f.version_id, v.document_id, f.article, f.part, "
        "f.point, f.structural_path, f.fragment_text, f.text_sha256, v.effective_from, "
        "v.effective_to, v.source_url, v.raw_sha256 "
        "FROM legal_fragments f "
        "JOIN legal_versions v ON v.id = f.version_id "
        "JOIN legal_sources s ON s.id = v.source_id "
        "WHERE v.approval_state = 'APPROVED' AND s.status = 'APPROVED'"
    )
