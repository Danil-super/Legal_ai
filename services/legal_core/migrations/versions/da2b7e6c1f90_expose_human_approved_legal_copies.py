"""Expose human-approved verified copies through the guarded production view.

Revision ID: da2b7e6c1f90
Revises: c8e5f1a2b6d4
"""

from collections.abc import Sequence

from alembic import op

revision: str = "da2b7e6c1f90"
down_revision: str | None = "c8e5f1a2b6d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _view(artifact_filter: str) -> str:
    return (
        "CREATE OR REPLACE VIEW production_legal_fragments AS "
        "SELECT f.id AS fragment_id, f.version_id, v.document_id, f.article, f.part, "
        "f.point, f.structural_path, f.fragment_text, f.text_sha256, v.effective_from, "
        "v.effective_to, v.source_url, v.raw_sha256, d.title AS document_title, "
        "d.issuer, d.official_number, v.version_date, v.publication_date "
        "FROM legal_fragments f "
        "JOIN legal_versions v ON v.id = f.version_id "
        "JOIN legal_sources s ON s.id = v.source_id "
        "JOIN legal_documents d ON d.id = v.document_id "
        "JOIN legal_approval_events ae ON ae.legal_version_id = v.id "
        "AND ae.actor_user_id = v.approved_by "
        "AND legal_approval_event_is_current(ae) "
        "WHERE v.approval_state = 'APPROVED' AND " + artifact_filter + " "
        "AND s.status = 'APPROVED'"
    )


def upgrade() -> None:
    op.execute(_view("v.artifact_kind IN ('OFFICIAL_RAW', 'THIRD_PARTY_VERIFIED_COPY')"))


def downgrade() -> None:
    # Only visibility is restored; immutable copies and human approval events remain intact.
    op.execute(_view("v.artifact_kind = 'OFFICIAL_RAW'"))
