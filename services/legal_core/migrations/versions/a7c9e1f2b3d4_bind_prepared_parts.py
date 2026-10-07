"""Bind immutable prepared parts to exact original corpus versions.

Revision ID: a7c9e1f2b3d4
Revises: f0a1b2c3d4e
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1f2b3d4"
down_revision: str | None = "f0a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legal_prepared_part_versions",
        sa.Column("id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("preparation_id", sa.Uuid(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("part_key", sa.String(120), nullable=False),
        sa.Column("part_text_sha256", sa.String(64), nullable=False),
        sa.Column("legal_version_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.ForeignKeyConstraint(["material_id"], ["legal_review_materials.id"],
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["preparation_id"], ["legal_material_preparations.id"],
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["legal_version_id"], ["legal_versions.id"],
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                ondelete="RESTRICT"),
        sa.UniqueConstraint("preparation_id", "part_key"),
        sa.UniqueConstraint("preparation_id", "legal_version_id"),
        sa.CheckConstraint("raw_sha256 ~ '^[0-9a-f]{64}$'"),
        sa.CheckConstraint("part_text_sha256 ~ '^[0-9a-f]{64}$'"),
    )
    op.create_index("ix_legal_prepared_part_versions_material",
                    "legal_prepared_part_versions", ["material_id", "preparation_id"])
    op.execute("""
        CREATE FUNCTION public.guard_prepared_part_version() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          prepared public.legal_material_preparations%ROWTYPE;
          original public.legal_review_materials%ROWTYPE;
          corpus public.legal_versions%ROWTYPE;
          document public.legal_documents%ROWTYPE;
          source public.legal_sources%ROWTYPE;
          item jsonb;
          part_entry jsonb;
          field_name text;
          start_pos integer;
          end_pos integer;
          scope_cursor integer := 0;
          part_count integer := 0;
          seen_keys text[] := ARRAY[]::text[];
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'prepared part/version associations are append-only';
          END IF;
          SELECT * INTO prepared FROM public.legal_material_preparations
            WHERE id = NEW.preparation_id FOR SHARE;
          SELECT * INTO original FROM public.legal_review_materials
            WHERE id = NEW.material_id FOR SHARE;
          SELECT * INTO corpus FROM public.legal_versions
            WHERE id = NEW.legal_version_id FOR SHARE;
          IF prepared.id IS NULL OR original.id IS NULL OR corpus.id IS NULL OR
             prepared.material_id IS DISTINCT FROM original.id OR
             prepared.raw_sha256 IS DISTINCT FROM NEW.raw_sha256 OR
             original.raw_sha256 IS DISTINCT FROM NEW.raw_sha256 OR
             corpus.raw_sha256 IS DISTINCT FROM NEW.raw_sha256 OR
             original.raw_bytes IS DISTINCT FROM corpus.raw_bytes OR
             original.kind <> 'LEGAL_COPY' OR
             original.raw_mime_type <> 'application/rtf' OR
             corpus.raw_mime_type <> original.raw_mime_type OR
             corpus.approval_state <> 'REVIEW_REQUIRED' OR
             corpus.artifact_kind <> 'THIRD_PARTY_VERIFIED_COPY' OR
             corpus.artifact_retrieved_at IS NULL OR
             corpus.normalization_scope <> 'FULL_DOCUMENT' OR
             corpus.parser_version IS DISTINCT FROM
               prepared.metadata_json->>'parser_version' OR
             prepared.kind <> 'NORMATIVE' OR
             prepared.metadata_json->>'extraction_scope' <> 'FULL_DOCUMENT' OR
             coalesce(prepared.metadata_json->>'source_locator', '') !~ '[^[:space:]]' OR
             coalesce(prepared.metadata_json->>'completeness_locator', '')
               !~ '[^[:space:]]' OR
             prepared.metadata_json->>'source_url' IS DISTINCT FROM corpus.source_url OR
             EXISTS (SELECT 1 FROM public.legal_material_preparations newer
                     WHERE newer.material_id = prepared.material_id
                       AND newer.revision > prepared.revision) THEN
            RAISE EXCEPTION 'prepared part does not match exact normative original';
          END IF;
          IF jsonb_typeof(prepared.metadata_json->'parts') IS DISTINCT FROM 'array' OR
             jsonb_typeof(prepared.metadata_json->'limitations') IS DISTINCT FROM 'array' OR
             jsonb_array_length(prepared.metadata_json->'limitations') <> 0 OR
             char_length(prepared.normalized_text) > 25000000 THEN
            RAISE EXCEPTION 'normative preparation is not a complete document';
          END IF;
          FOR part_entry IN SELECT value FROM jsonb_array_elements(
            prepared.metadata_json->'parts'
          ) LOOP
            part_count := part_count + 1;
            IF part_count > 20 OR
               coalesce(part_entry->>'part_key', '') !~ '^[a-z0-9][a-z0-9-]*$' OR
               char_length(part_entry->>'part_key') > 120 OR
               part_entry->>'part_key' = ANY(seen_keys) OR
               jsonb_typeof(part_entry->'text_start') IS DISTINCT FROM 'number' OR
               jsonb_typeof(part_entry->'text_end') IS DISTINCT FROM 'number' OR
               coalesce(part_entry->>'text_start', '') !~ '^(0|[1-9][0-9]*)$' OR
               coalesce(part_entry->>'text_end', '') !~ '^[1-9][0-9]*$' THEN
              RAISE EXCEPTION 'normative preparation has an invalid part';
            END IF;
            seen_keys := array_append(seen_keys, part_entry->>'part_key');
            start_pos := (part_entry->>'text_start')::integer;
            end_pos := (part_entry->>'text_end')::integer;
            IF start_pos IS DISTINCT FROM scope_cursor OR end_pos <= start_pos OR
               end_pos > char_length(prepared.normalized_text) OR
               part_entry->>'text_sha256' IS DISTINCT FROM encode(digest(
                 convert_to(substring(prepared.normalized_text FROM start_pos + 1
                            FOR end_pos - start_pos), 'UTF8'), 'sha256'), 'hex') THEN
              RAISE EXCEPTION 'normative part scopes must cover the complete text';
            END IF;
            IF part_entry->>'part_key' = NEW.part_key THEN
              item := part_entry;
            END IF;
            scope_cursor := end_pos;
          END LOOP;
          IF part_count = 0 OR scope_cursor <> char_length(prepared.normalized_text) THEN
            RAISE EXCEPTION 'normative part scopes do not cover the complete text';
          END IF;
          IF item IS NULL OR item->>'text_sha256' IS DISTINCT FROM NEW.part_text_sha256 OR
             NEW.part_text_sha256 IS DISTINCT FROM corpus.normalized_sha256 THEN
            RAISE EXCEPTION 'prepared part scope is missing or changed';
          END IF;
          start_pos := (item->>'text_start')::integer;
          end_pos := (item->>'text_end')::integer;
          IF start_pos IS NULL OR end_pos IS NULL OR start_pos < 0 OR
             end_pos <= start_pos OR end_pos > char_length(prepared.normalized_text) OR
             substring(prepared.normalized_text FROM start_pos + 1
                       FOR end_pos - start_pos) IS DISTINCT FROM corpus.normalized_text OR
             encode(digest(convert_to(corpus.normalized_text, 'UTF8'), 'sha256'), 'hex')
               IS DISTINCT FROM NEW.part_text_sha256 THEN
            RAISE EXCEPTION 'prepared part text differs from corpus version';
          END IF;
          SELECT * INTO document FROM public.legal_documents
            WHERE id = corpus.document_id FOR SHARE;
          IF document.id IS NULL OR
             item->>'canonical_key' IS DISTINCT FROM document.canonical_key OR
             item->>'document_type' IS DISTINCT FROM document.document_type OR
             item->>'title' IS DISTINCT FROM document.title OR
             item->>'issuer' IS DISTINCT FROM document.issuer OR
             item->>'official_number' IS DISTINCT FROM document.official_number OR
             (item->>'adoption_date')::date IS DISTINCT FROM document.adoption_date OR
             (item->>'publication_date')::date IS DISTINCT FROM corpus.publication_date OR
             (item->>'version_date')::date IS DISTINCT FROM corpus.version_date OR
             (item->>'effective_from')::date IS DISTINCT FROM corpus.effective_from OR
             (item->>'effective_to')::date IS DISTINCT FROM corpus.effective_to THEN
            RAISE EXCEPTION 'canonical identity or evidenced edition differs';
          END IF;
          FOREACH field_name IN ARRAY ARRAY[
            'title', 'canonical_key', 'document_type', 'issuer', 'official_number',
            'adoption_date', 'publication_date', 'version_date', 'effective_from'
          ] LOOP
            IF coalesce(item->>field_name, '') !~ '[^[:space:]]' OR
               coalesce(item->'evidence'->>field_name, '') !~ '[^[:space:]]' THEN
              RAISE EXCEPTION 'canonical field lacks evidence';
            END IF;
          END LOOP;
          IF item->>'effective_to' IS NOT NULL AND
             coalesce(item->'evidence'->>'effective_to', '') !~ '[^[:space:]]' THEN
            RAISE EXCEPTION 'effective-to date lacks evidence';
          END IF;
          SELECT * INTO source FROM public.legal_sources
            WHERE id = corpus.source_id FOR SHARE;
          IF source.id IS NULL OR source.source_key <> 'garant' OR
             source.trust_level <> 'VERIFIED_COPY' OR
             source.allowed_hosts <> '["internet.garant.ru"]'::jsonb OR
             source.status NOT IN ('DRAFT', 'APPROVED') OR
             corpus.source_url !~
               '^https://internet[.]garant[.]ru/document/redirect/[0-9]{1,20}/0$' OR
             corpus.source_external_id IS DISTINCT FROM
               substring(corpus.source_url FROM '/([0-9]{1,20})/0$') OR
             NOT EXISTS (SELECT 1 FROM public.legal_fragments
                         WHERE version_id = corpus.id) OR
             NOT EXISTS (SELECT 1 FROM public.users
                         WHERE id = NEW.created_by_user_id AND status = 'ACTIVE'
                           AND system_role = 'LEGAL_EDITOR' FOR SHARE) THEN
            RAISE EXCEPTION 'source, fragments or operator are ineligible';
          END IF;
          RETURN NEW;
        END;
        $$;
        REVOKE ALL ON FUNCTION public.guard_prepared_part_version() FROM PUBLIC;
        CREATE TRIGGER legal_prepared_part_versions_guard
          BEFORE INSERT OR UPDATE OR DELETE ON public.legal_prepared_part_versions
          FOR EACH ROW EXECUTE FUNCTION public.guard_prepared_part_version();
    """)


def downgrade() -> None:
    op.drop_table("legal_prepared_part_versions")
    op.execute("DROP FUNCTION public.guard_prepared_part_version()")
