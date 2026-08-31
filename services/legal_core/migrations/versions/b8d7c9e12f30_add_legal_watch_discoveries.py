"""add immutable pre-classification legal watch discoveries

Revision ID: b8d7c9e12f30
Revises: a6b3d2e8f410
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b8d7c9e12f30"
down_revision: str | None = "a6b3d2e8f410"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE legal_watch_discoveries (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            eo_number varchar(24) NOT NULL UNIQUE,
            title text NOT NULL,
            document_number varchar(120),
            document_date date,
            publication_date date NOT NULL,
            source_url text NOT NULL,
            pdf_sha256 varchar(64) NOT NULL,
            pdf_size_bytes bigint NOT NULL,
            matched_rule_ids_json jsonb NOT NULL,
            quarantine_ref varchar(180) NOT NULL,
            candidate_sha256 varchar(64) NOT NULL UNIQUE,
            status varchar(30) NOT NULL DEFAULT 'REVIEW_REQUIRED',
            staged_at timestamptz NOT NULL,
            imported_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
            CONSTRAINT ck_legal_watch_discoveries_eo_number
                CHECK (eo_number ~ '^[0-9]{16,24}$'),
            CONSTRAINT ck_legal_watch_discoveries_title
                CHECK (char_length(title) BETWEEN 1 AND 4000),
            CONSTRAINT ck_legal_watch_discoveries_source
                CHECK (source_url LIKE 'https://publication.pravo.gov.ru/%'),
            CONSTRAINT ck_legal_watch_discoveries_pdf_sha
                CHECK (pdf_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_legal_watch_discoveries_pdf_size
                CHECK (pdf_size_bytes BETWEEN 5 AND 52428800),
            CONSTRAINT ck_legal_watch_discoveries_rules
                CHECK (
                    jsonb_typeof(matched_rule_ids_json) = 'array'
                    AND jsonb_array_length(matched_rule_ids_json) BETWEEN 1 AND 50
                ),
            CONSTRAINT ck_legal_watch_discoveries_quarantine_ref
                CHECK (
                    char_length(quarantine_ref) BETWEEN 1 AND 180
                    AND quarantine_ref NOT LIKE '%..%'
                ),
            CONSTRAINT ck_legal_watch_discoveries_candidate_sha
                CHECK (candidate_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_legal_watch_discoveries_status
                CHECK (status = 'REVIEW_REQUIRED')
        )
        """
    )
    op.create_index(
        "ix_legal_watch_discoveries_review_queue",
        "legal_watch_discoveries",
        ["status", "publication_date", "imported_at", "id"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER legal_watch_discoveries_append_only
        BEFORE UPDATE OR DELETE ON legal_watch_discoveries
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS legal_watch_discoveries_append_only "
        "ON legal_watch_discoveries"
    )
    op.drop_index(
        "ix_legal_watch_discoveries_review_queue",
        table_name="legal_watch_discoveries",
    )
    op.drop_table("legal_watch_discoveries")
