"""Human-reviewed current copies without invented edition metadata (ADR 0075).

Revision ID: c0f6e8a2d419
Revises: b9e5f3a7c012
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "c0f6e8a2d419"
down_revision: str | None = "b9e5f3a7c012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(value: str, old: str, new: str) -> str:
    if value.count(old) != 1:
        raise RuntimeError("unexpected predecessor guard definition")
    return value.replace(old, new)


def _dated_approval_function() -> str:
    path = Path(__file__).with_name("c8e5f1a2b6d4_allow_reviewed_consultant_copies.py")
    spec = importlib.util.spec_from_file_location("dated_legal_guard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("predecessor approval guard is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module._approval_event_current_function())


def _approval_function() -> str:
    # Retain every common checksum, source, fragment and human-role check of v1/v2.
    value = _dated_approval_function()
    value = _replace(
        value,
        "AND v.normalization_scope = 'FULL_DOCUMENT'",
        """
      AND ((v.date_basis = 'DATED_EDITION' AND v.normalization_scope = 'FULL_DOCUMENT')
        OR (v.date_basis = 'LAWYER_CURRENT_COPY' AND v.normalization_scope = 'TEXT_LAYER'
            AND jsonb_array_length(v.extraction_limitations) > 0))""",
    )
    value = _replace(
        value,
        "AND v.effective_from = DATE '2023-09-01'",
        """
      AND ((v.date_basis = 'DATED_EDITION' AND v.effective_from = DATE '2023-09-01')
        OR (v.date_basis = 'LAWYER_CURRENT_COPY' AND v.effective_from >= DATE '2023-09-01'
            AND v.effective_from < DATE '2026-09-01'))""",
    )
    value = _replace(
        value,
        "AND v.effective_from = DATE '2026-09-01'",
        """
      AND ((v.date_basis = 'DATED_EDITION' AND v.effective_from = DATE '2026-09-01')
        OR (v.date_basis = 'LAWYER_CURRENT_COPY' AND v.effective_from >= DATE '2026-09-01'
            AND v.effective_from < DATE '2031-09-01'))""",
    )
    start = value.index("               AND (\n                     ((approval_event)")
    end = value.index("               AND (approval_event).checks_json ?", start)
    legacy_predicate = value[start:end].strip().removeprefix("AND ")
    current_predicate = """
      (v.date_basis = 'LAWYER_CURRENT_COPY'
       AND (approval_event).policy_version = 'dental-legal-approval.v3'
       AND v.artifact_kind = 'THIRD_PARTY_VERIFIED_COPY' AND s.trust_level = 'VERIFIED_COPY'
       AND v.raw_mime_type = 'application/rtf'
       AND substring(v.raw_bytes FROM 1 FOR 5) = decode('7b5c727466', 'hex')
       AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY['object','objdata','objclass','objupdate']) c
         WHERE position(repeat(chr(92), 2) || c IN lower(encode(v.raw_bytes, 'escape'))) > 0)
       AND (approval_event).checks_json @> jsonb_build_object(
         'sourceIsOfficial', false, 'officialTextCompared', true,
         'artifactIsComplete', true, 'fragmentsVerified', true,
         'currentCopyConfirmed', true, 'extractionLimitsUnderstood', true,
         'expectedNormalizedSha256', v.normalized_sha256,
         'expectedFragmentsSha256', v.fragments_sha256,
         'expectedEffectiveFrom', v.effective_from::text)
       AND (approval_event).regression_checks_json @> jsonb_build_object(
         'dateBasis', v.date_basis, 'extractionLimitations', v.extraction_limitations)
       AND (approval_event).regression_checks_json->'extractionLimitations'
         = v.extraction_limitations)
    """
    return (
        value[:start]
        + (
            " AND ((v.date_basis = 'DATED_EDITION' AND "
            + legacy_predicate
            + ") OR "
            + current_predicate
            + ")\n"
        )
        + value[end:]
    )


def _bind_function(original: str) -> str:
    value = _replace(
        original,
        "corpus.normalization_scope <> 'FULL_DOCUMENT'",
        """
      (corpus.normalization_scope IS DISTINCT FROM CASE
        WHEN corpus.date_basis = 'LAWYER_CURRENT_COPY' THEN 'TEXT_LAYER'
        ELSE 'FULL_DOCUMENT' END)""",
    )
    value = _replace(
        value,
        "prepared.metadata_json->>'extraction_scope' <> 'FULL_DOCUMENT'",
        """
      (prepared.metadata_json->>'extraction_scope' IS DISTINCT FROM CASE
        WHEN corpus.date_basis = 'LAWYER_CURRENT_COPY' THEN 'PARTIAL'
        ELSE 'FULL_DOCUMENT' END)""",
    )
    value = _replace(
        value,
        "jsonb_array_length(prepared.metadata_json->'limitations') <> 0",
        """
      ((corpus.date_basis = 'DATED_EDITION'
         AND jsonb_array_length(prepared.metadata_json->'limitations') <> 0)
       OR (corpus.date_basis = 'LAWYER_CURRENT_COPY' AND
         (jsonb_array_length(prepared.metadata_json->'limitations') = 0
          OR prepared.metadata_json->'limitations' IS DISTINCT FROM corpus.extraction_limitations)))
      """,
    )
    value = _replace(
        value,
        "(item->>'effective_from')::date IS DISTINCT FROM corpus.effective_from",
        """
      (CASE WHEN corpus.date_basis = 'LAWYER_CURRENT_COPY'
        THEN item->>'copy_valid_from' ELSE item->>'effective_from' END)::date
        IS DISTINCT FROM corpus.effective_from
      OR coalesce(item->>'date_basis', 'DATED_EDITION') IS DISTINCT FROM corpus.date_basis
      OR (corpus.date_basis = 'LAWYER_CURRENT_COPY' AND item->>'effective_from' IS NOT NULL)
      """,
    )
    value = _replace(
        value,
        "FOREACH field_name IN ARRAY ARRAY[",
        """
      FOREACH field_name IN ARRAY CASE WHEN corpus.date_basis = 'LAWYER_CURRENT_COPY'
        THEN ARRAY['title', 'canonical_key', 'document_type', 'adoption_date', 'copy_valid_from']
        ELSE ARRAY[""",
    )
    value = _replace(value, "          ] LOOP", "          ] END LOOP")
    return value


def _production_view() -> str:
    # A query-local fence, not a cache: every query rechecks editor/source/event and all hashes.
    # Without the fence PostgreSQL repeats the full-version guard for every matching fragment.
    return """
      CREATE OR REPLACE VIEW public.production_legal_fragments AS
      WITH audited_versions AS MATERIALIZED (
        SELECT v.id AS version_id, v.document_id, v.effective_from, v.effective_to,
               v.source_url, v.raw_sha256, d.title AS document_title, d.issuer,
               d.official_number, v.version_date, v.publication_date,
               v.date_basis, v.extraction_limitations
          FROM public.legal_versions v
          JOIN public.legal_sources s ON s.id = v.source_id
          JOIN public.legal_documents d ON d.id = v.document_id
         WHERE v.approval_state = 'APPROVED' AND s.status = 'APPROVED'
           AND v.artifact_kind IN ('OFFICIAL_RAW', 'THIRD_PARTY_VERIFIED_COPY')
           AND EXISTS (
             SELECT 1 FROM public.legal_approval_events ae
              WHERE ae.legal_version_id = v.id AND ae.actor_user_id = v.approved_by
                AND public.legal_approval_event_is_current(ae)
           )
      )
      SELECT f.id AS fragment_id, f.version_id, a.document_id, f.article, f.part, f.point,
             f.structural_path, f.fragment_text, f.text_sha256, a.effective_from,
             a.effective_to, a.source_url, a.raw_sha256, a.document_title, a.issuer,
             a.official_number, a.version_date, a.publication_date,
             a.date_basis, a.extraction_limitations
        FROM public.legal_fragments f JOIN audited_versions a ON a.version_id = f.version_id
    """


def upgrade() -> None:
    op.alter_column("legal_documents", "issuer", existing_type=sa.String(240), nullable=True)
    op.add_column(
        "legal_versions",
        sa.Column("date_basis", sa.String(30), nullable=False, server_default="DATED_EDITION"),
    )
    op.add_column(
        "legal_versions",
        sa.Column(
            "extraction_limitations",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.drop_constraint("ck_legal_versions_normalization_scope", "legal_versions", type_="check")
    op.create_check_constraint(
        "ck_legal_versions_normalization_scope",
        "legal_versions",
        "normalization_scope IN ('SELECTED_EXCERPT', 'FULL_DOCUMENT', 'TEXT_LAYER')",
    )
    op.drop_constraint("ck_legal_versions_reviewable_metadata", "legal_versions", type_="check")
    op.create_check_constraint(
        "ck_legal_versions_reviewable_metadata",
        "legal_versions",
        "artifact_kind NOT IN ('OFFICIAL_RAW', 'THIRD_PARTY_VERIFIED_COPY') OR "
        "(artifact_retrieved_at IS NOT NULL AND (normalization_scope = 'FULL_DOCUMENT' "
        "OR (normalization_scope = 'TEXT_LAYER' AND date_basis = 'LAWYER_CURRENT_COPY')) "
        "AND (raw_mime_type <> 'application/pdf' OR artifact_page_count IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_legal_versions_date_basis",
        "legal_versions",
        "date_basis IN ('DATED_EDITION', 'LAWYER_CURRENT_COPY')",
    )
    op.create_check_constraint(
        "ck_legal_versions_extraction_limitations",
        "legal_versions",
        "jsonb_typeof(extraction_limitations) = 'array'",
    )
    op.create_check_constraint(
        "ck_legal_versions_current_copy",
        "legal_versions",
        "date_basis <> 'LAWYER_CURRENT_COPY' OR (artifact_kind = 'THIRD_PARTY_VERIFIED_COPY' "
        "AND normalization_scope = 'TEXT_LAYER' "
        "AND jsonb_array_length(extraction_limitations) > 0)",
    )
    op.execute("""
      CREATE FUNCTION public.protect_current_copy_metadata() RETURNS trigger
      LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ BEGIN
        IF NEW.date_basis IS DISTINCT FROM OLD.date_basis OR
           NEW.extraction_limitations IS DISTINCT FROM OLD.extraction_limitations THEN
          RAISE EXCEPTION 'legal copy applicability and extraction metadata are immutable';
        END IF;
        RETURN NEW;
      END $$;
      REVOKE ALL ON FUNCTION public.protect_current_copy_metadata() FROM PUBLIC;
      CREATE TRIGGER legal_current_copy_metadata_immutable BEFORE UPDATE ON public.legal_versions
        FOR EACH ROW EXECUTE FUNCTION public.protect_current_copy_metadata();
    """)
    op.execute(_approval_function())
    op.execute(_production_view())
    original = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef('public.guard_prepared_part_version()'::regprocedure)")
    )
    if not isinstance(original, str):
        raise RuntimeError("binding guard is missing")
    backup = _replace(
        original,
        "public.guard_prepared_part_version()",
        "public.guard_prepared_part_version_dated_backup()",
    )
    op.execute(backup)
    op.execute(
        "REVOKE ALL ON FUNCTION public.guard_prepared_part_version_dated_backup() FROM PUBLIC"
    )
    op.execute(_bind_function(original))


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM legal_versions WHERE date_basis = 'LAWYER_CURRENT_COPY') OR
           EXISTS (SELECT 1 FROM legal_approval_events
                   WHERE policy_version = 'dental-legal-approval.v3') OR
           EXISTS (SELECT 1 FROM legal_documents WHERE issuer IS NULL) THEN
          RAISE EXCEPTION 'cannot downgrade while current-copy evidence is retained';
        END IF;
      END $$;
    """)
    backup = op.get_bind().scalar(
        sa.text(
            "SELECT pg_get_functiondef("
            "'public.guard_prepared_part_version_dated_backup()'::regprocedure)"
        )
    )
    if not isinstance(backup, str):
        raise RuntimeError("dated binding guard is missing")
    op.execute(
        _replace(
            backup,
            "public.guard_prepared_part_version_dated_backup()",
            "public.guard_prepared_part_version()",
        )
    )
    op.execute("DROP FUNCTION public.guard_prepared_part_version_dated_backup()")
    op.execute(_dated_approval_function())
    # No dependent schema objects are allowed to be discarded. Existing default SELECT grants
    # from runtime-role provisioning apply to the replacement view as to all other public tables.
    op.execute("DROP VIEW public.production_legal_fragments")
    op.execute("""
      CREATE VIEW public.production_legal_fragments AS
      SELECT f.id AS fragment_id, f.version_id, v.document_id, f.article, f.part, f.point,
             f.structural_path, f.fragment_text, f.text_sha256, v.effective_from,
             v.effective_to, v.source_url, v.raw_sha256, d.title AS document_title, d.issuer,
             d.official_number, v.version_date, v.publication_date
        FROM public.legal_fragments f JOIN public.legal_versions v ON v.id = f.version_id
        JOIN public.legal_sources s ON s.id = v.source_id
        JOIN public.legal_documents d ON d.id = v.document_id
        JOIN public.legal_approval_events ae ON ae.legal_version_id = v.id
          AND ae.actor_user_id = v.approved_by AND public.legal_approval_event_is_current(ae)
       WHERE v.approval_state = 'APPROVED' AND s.status = 'APPROVED'
         AND v.artifact_kind IN ('OFFICIAL_RAW', 'THIRD_PARTY_VERIFIED_COPY')
    """)
    op.execute("DROP TRIGGER legal_current_copy_metadata_immutable ON public.legal_versions")
    op.execute("DROP FUNCTION public.protect_current_copy_metadata()")
    for name in (
        "current_copy",
        "extraction_limitations",
        "date_basis",
        "reviewable_metadata",
        "normalization_scope",
    ):
        op.drop_constraint("ck_legal_versions_" + name, "legal_versions", type_="check")
    op.create_check_constraint(
        "ck_legal_versions_normalization_scope",
        "legal_versions",
        "normalization_scope IN ('SELECTED_EXCERPT', 'FULL_DOCUMENT')",
    )
    op.create_check_constraint(
        "ck_legal_versions_reviewable_metadata",
        "legal_versions",
        "artifact_kind NOT IN ('OFFICIAL_RAW', 'THIRD_PARTY_VERIFIED_COPY') OR "
        "(artifact_retrieved_at IS NOT NULL AND normalization_scope = 'FULL_DOCUMENT' "
        "AND (raw_mime_type <> 'application/pdf' OR artifact_page_count IS NOT NULL))",
    )
    op.drop_column("legal_versions", "extraction_limitations")
    op.drop_column("legal_versions", "date_basis")
    op.alter_column("legal_documents", "issuer", existing_type=sa.String(240), nullable=False)
