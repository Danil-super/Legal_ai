"""add immutable pgvector embeddings for approved legal fragments

Revision ID: a6b3d2e8f410
Revises: e73f2a8c4d19
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a6b3d2e8f410"
down_revision: str | None = "e73f2a8c4d19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE legal_fragment_embeddings (
            fragment_id uuid NOT NULL,
            model_key varchar(120) NOT NULL,
            dimensions integer NOT NULL,
            embedding vector NOT NULL,
            fragment_text_sha256 varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
            CONSTRAINT pk_legal_fragment_embeddings PRIMARY KEY (fragment_id, model_key),
            CONSTRAINT fk_legal_fragment_embeddings_fragment
                FOREIGN KEY (fragment_id) REFERENCES legal_fragments(id) ON DELETE RESTRICT,
            CONSTRAINT ck_legal_fragment_embeddings_dimensions
                CHECK (dimensions BETWEEN 1 AND 4096),
            CONSTRAINT ck_legal_fragment_embeddings_vector_dimensions
                CHECK (vector_dims(embedding) = dimensions),
            CONSTRAINT ck_legal_fragment_embeddings_text_sha256
                CHECK (fragment_text_sha256 ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.create_index(
        "ix_legal_fragment_embeddings_model",
        "legal_fragment_embeddings",
        ["model_key", "dimensions", "fragment_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION legal_fragment_embeddings_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'legal fragment embeddings are append-only; use a new model_key';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER legal_fragment_embeddings_append_only_trigger
        BEFORE UPDATE OR DELETE ON legal_fragment_embeddings
        FOR EACH ROW EXECUTE FUNCTION legal_fragment_embeddings_append_only()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS legal_fragment_embeddings_append_only_trigger "
        "ON legal_fragment_embeddings"
    )
    op.execute("DROP FUNCTION IF EXISTS legal_fragment_embeddings_append_only()")
    op.drop_index(
        "ix_legal_fragment_embeddings_model",
        table_name="legal_fragment_embeddings",
    )
    op.drop_table("legal_fragment_embeddings")
    # The vector extension may be shared by other database objects; do not drop it automatically.
