"""Add immutable preparation revisions without modifying incoming receipts."""

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_review_material_id_sha", "legal_review_materials",
                                ["id", "raw_sha256"])
    op.create_table(
        "legal_material_preparations",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("preparation_sha256", sa.String(64), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("group_key", sa.String(30), nullable=False),
        sa.Column("metadata_json", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.ForeignKeyConstraint(["material_id", "raw_sha256"],
                                ["legal_review_materials.id", "legal_review_materials.raw_sha256"],
                                ondelete="RESTRICT"),
        sa.UniqueConstraint("material_id", "revision"),
        sa.UniqueConstraint("material_id", "preparation_sha256"),
        sa.CheckConstraint("revision > 0"),
        sa.CheckConstraint("kind IN ('NORMATIVE','CLINICAL_REFERENCE','REFERENCE_FORM')"),
        sa.CheckConstraint("group_key IN ('clinical','labour','courts','privacy',"
                           "'licensing','healthcare','general')"),
        sa.CheckConstraint("preparation_sha256 = legal_regression_result_sha256(metadata_json)"),
        sa.CheckConstraint(
            "(metadata_json->>'normalized_sha256' IS NULL AND normalized_text = '') OR "
            "(metadata_json->>'normalized_sha256' IS NOT NULL AND "
            "metadata_json->>'normalized_sha256' = "
            "encode(digest(convert_to(normalized_text, 'UTF8'), 'sha256'), 'hex'))"
        ),
    )
    op.execute("""
        CREATE FUNCTION guard_material_preparation() RETURNS trigger AS $$
        DECLARE original_kind text; latest integer;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'material preparation is append-only';
          END IF;
          SELECT kind INTO original_kind FROM legal_review_materials
            WHERE id = NEW.material_id FOR UPDATE;
          IF original_kind IS NULL OR
             ((original_kind = 'CLINICAL_REFERENCE') <>
              (NEW.kind = 'CLINICAL_REFERENCE')) THEN
            RAISE EXCEPTION 'preparation kind conflicts with original';
          END IF;
          IF NEW.metadata_json->>'title' IS DISTINCT FROM NEW.title OR
             NEW.metadata_json->>'kind' IS DISTINCT FROM NEW.kind OR
             NEW.metadata_json->>'group_key' IS DISTINCT FROM NEW.group_key OR
             NEW.metadata_json->>'raw_sha256' IS DISTINCT FROM NEW.raw_sha256 THEN
            RAISE EXCEPTION 'preparation metadata mismatch';
          END IF;
          SELECT coalesce(max(revision), 0) INTO latest FROM legal_material_preparations
            WHERE material_id = NEW.material_id;
          IF NEW.revision <> latest + 1 THEN
            RAISE EXCEPTION 'preparation revision out of order';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER legal_material_preparation_guard
          BEFORE INSERT OR UPDATE OR DELETE ON legal_material_preparations
          FOR EACH ROW EXECUTE FUNCTION guard_material_preparation();
    """)


def downgrade() -> None:
    op.drop_table("legal_material_preparations")
    op.execute("DROP FUNCTION guard_material_preparation()")
    op.drop_constraint("uq_review_material_id_sha", "legal_review_materials", type_="unique")
