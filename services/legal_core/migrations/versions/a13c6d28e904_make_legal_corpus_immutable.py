"""make legal corpus immutable

Revision ID: a13c6d28e904
Revises: f4e21a8c9b37
Create Date: 2026-08-22 17:38:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a13c6d28e904"
down_revision: str | None = "f4e21a8c9b37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("legal_versions", sa.Column("raw_size_bytes", sa.BigInteger(), nullable=True))
    op.add_column(
        "legal_versions", sa.Column("artifact_retrieved_at", sa.DateTime(timezone=True))
    )
    op.add_column("legal_versions", sa.Column("artifact_page_count", sa.Integer()))
    op.add_column("legal_versions", sa.Column("normalized_sha256", sa.String(length=64)))
    op.add_column("legal_versions", sa.Column("fragments_sha256", sa.String(length=64)))
    op.add_column("legal_versions", sa.Column("normalization_scope", sa.String(length=30)))
    op.execute(
        "UPDATE legal_versions SET raw_mime_type = "
        "'application/vnd.dental-legal.normalized-excerpt+json' "
        "WHERE artifact_kind = 'NORMALIZED_EXCERPT' AND raw_mime_type = "
        "'application/vnd.dental-legal.official-excerpt+json'"
    )
    op.execute("UPDATE legal_versions SET raw_size_bytes = octet_length(raw_bytes)")
    op.execute(
        "UPDATE legal_versions SET normalized_sha256 = "
        "encode(digest(convert_to(normalized_text, 'UTF8'), 'sha256'), 'hex')"
    )
    op.execute(
        "UPDATE legal_versions v SET fragments_sha256 = encode(digest(convert_to("
        "coalesce((SELECT string_agg(f.ordinal::text || ':' || f.text_sha256, E'\\n' "
        "ORDER BY f.ordinal) FROM legal_fragments f WHERE f.version_id = v.id), ''), "
        "'UTF8'), 'sha256'), 'hex')"
    )
    op.execute("UPDATE legal_versions SET normalization_scope = 'SELECTED_EXCERPT'")
    op.alter_column("legal_versions", "raw_size_bytes", nullable=False)
    op.alter_column("legal_versions", "normalized_sha256", nullable=False)
    op.alter_column("legal_versions", "fragments_sha256", nullable=False)
    op.alter_column("legal_versions", "normalization_scope", nullable=False)
    op.create_check_constraint(
        "ck_legal_versions_raw_size",
        "legal_versions",
        "octet_length(raw_bytes) = raw_size_bytes",
    )
    op.create_check_constraint(
        "ck_legal_versions_normalized_sha256",
        "legal_versions",
        "encode(digest(convert_to(normalized_text, 'UTF8'), 'sha256'), 'hex') "
        "= normalized_sha256",
    )
    op.create_check_constraint(
        "ck_legal_versions_normalization_scope",
        "legal_versions",
        "normalization_scope IN ('SELECTED_EXCERPT', 'FULL_DOCUMENT')",
    )
    op.create_check_constraint(
        "ck_legal_versions_official_metadata",
        "legal_versions",
        "artifact_kind <> 'OFFICIAL_RAW' OR "
        "(artifact_retrieved_at IS NOT NULL AND normalization_scope = 'FULL_DOCUMENT' "
        "AND (raw_mime_type <> 'application/pdf' OR artifact_page_count IS NOT NULL))",
    )
    op.add_column(
        "legal_approval_events",
        sa.Column(
            "policy_version",
            sa.String(length=80),
            server_default="dental-legal-approval.v1",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_legal_approval_events_policy_version",
        "legal_approval_events",
        "char_length(policy_version) BETWEEN 1 AND 80",
    )
    op.add_column(
        "legal_approval_events",
        sa.Column(
            "regression_result_sha256",
            sa.String(length=64),
            server_default="0" * 64,
            nullable=False,
        ),
    )
    op.add_column(
        "legal_approval_events",
        sa.Column(
            "regression_checks_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_legal_approval_events_regression_sha256",
        "legal_approval_events",
        "char_length(regression_result_sha256) = 64",
    )
    op.execute(
        """
        CREATE FUNCTION protect_legal_version_immutable_fields()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'legal versions are immutable';
          END IF;
          IF OLD.document_id IS DISTINCT FROM NEW.document_id
             OR OLD.source_id IS DISTINCT FROM NEW.source_id
             OR OLD.version_no IS DISTINCT FROM NEW.version_no
             OR OLD.source_external_id IS DISTINCT FROM NEW.source_external_id
             OR OLD.source_url IS DISTINCT FROM NEW.source_url
             OR OLD.publication_date IS DISTINCT FROM NEW.publication_date
             OR OLD.version_date IS DISTINCT FROM NEW.version_date
             OR OLD.effective_from IS DISTINCT FROM NEW.effective_from
             OR OLD.effective_to IS DISTINCT FROM NEW.effective_to
             OR OLD.artifact_kind IS DISTINCT FROM NEW.artifact_kind
             OR OLD.raw_sha256 IS DISTINCT FROM NEW.raw_sha256
             OR OLD.raw_mime_type IS DISTINCT FROM NEW.raw_mime_type
             OR OLD.raw_bytes IS DISTINCT FROM NEW.raw_bytes
             OR OLD.raw_size_bytes IS DISTINCT FROM NEW.raw_size_bytes
             OR OLD.artifact_retrieved_at IS DISTINCT FROM NEW.artifact_retrieved_at
             OR OLD.artifact_page_count IS DISTINCT FROM NEW.artifact_page_count
             OR OLD.normalized_text IS DISTINCT FROM NEW.normalized_text
             OR OLD.normalized_sha256 IS DISTINCT FROM NEW.normalized_sha256
             OR OLD.fragments_sha256 IS DISTINCT FROM NEW.fragments_sha256
             OR OLD.normalization_scope IS DISTINCT FROM NEW.normalization_scope
             OR OLD.parser_version IS DISTINCT FROM NEW.parser_version
             OR OLD.received_at IS DISTINCT FROM NEW.received_at THEN
            RAISE EXCEPTION 'legal version content and applicability are immutable';
          END IF;
          IF OLD.approval_state = 'REVIEW_REQUIRED'
             AND NEW.approval_state = 'APPROVED' THEN
            IF NEW.approved_by IS NULL OR NEW.approved_at IS NULL
               OR NOT NEW.regression_passed
               OR NOT EXISTS (
                 SELECT 1
                   FROM legal_approval_events e
                  WHERE e.legal_version_id = NEW.id
                    AND e.actor_user_id = NEW.approved_by
                    AND e.expected_sha256 = NEW.raw_sha256
                    AND e.decision = 'APPROVED'
                    AND e.policy_version = 'dental-legal-approval.v1'
                    AND e.regression_checks_json ->> 'passed' = 'true'
               ) THEN
              RAISE EXCEPTION 'approved legal version requires matching approval event';
            END IF;
          ELSIF OLD.approval_state IS DISTINCT FROM NEW.approval_state
             OR OLD.regression_passed IS DISTINCT FROM NEW.regression_passed
             OR OLD.approved_by IS DISTINCT FROM NEW.approved_by
             OR OLD.approved_at IS DISTINCT FROM NEW.approved_at THEN
            RAISE EXCEPTION 'legal approval state transition is not allowed';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER legal_versions_protect_content
        BEFORE UPDATE OR DELETE ON legal_versions
        FOR EACH ROW EXECUTE FUNCTION protect_legal_version_immutable_fields()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_legal_fragment_mutation()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF EXISTS (
              SELECT 1 FROM legal_versions v
               WHERE v.id = NEW.version_id AND v.approval_state = 'APPROVED'
            ) THEN
              RAISE EXCEPTION 'cannot append fragments to an approved legal version';
            END IF;
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'legal fragments are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER legal_fragments_append_only
        BEFORE INSERT OR UPDATE OR DELETE ON legal_fragments
        FOR EACH ROW EXECUTE FUNCTION guard_legal_fragment_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_legal_source_identity()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'legal sources are immutable';
          END IF;
          IF OLD.source_key IS DISTINCT FROM NEW.source_key
             OR OLD.revision IS DISTINCT FROM NEW.revision
             OR OLD.display_name IS DISTINCT FROM NEW.display_name
             OR OLD.base_url IS DISTINCT FROM NEW.base_url
             OR OLD.allowed_hosts IS DISTINCT FROM NEW.allowed_hosts
             OR OLD.trust_level IS DISTINCT FROM NEW.trust_level
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'legal source identity is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER legal_sources_protect_identity
        BEFORE UPDATE OR DELETE ON legal_sources
        FOR EACH ROW EXECUTE FUNCTION protect_legal_source_identity()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_legal_document_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'legal documents are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER legal_documents_immutable
        BEFORE UPDATE OR DELETE ON legal_documents
        FOR EACH ROW EXECUTE FUNCTION prevent_legal_document_mutation()
        """
    )
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
        "JOIN legal_approval_events ae ON ae.legal_version_id = v.id "
        "AND ae.decision = 'APPROVED' AND ae.actor_user_id = v.approved_by "
        "AND ae.expected_sha256 = v.raw_sha256 "
        "AND ae.policy_version = 'dental-legal-approval.v1' "
        "AND ae.regression_checks_json ->> 'passed' = 'true' "
        "WHERE v.approval_state = 'APPROVED' AND v.artifact_kind = 'OFFICIAL_RAW' "
        "AND s.status = 'APPROVED'"
    )


def downgrade() -> None:
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
        "WHERE v.approval_state = 'APPROVED' AND v.artifact_kind = 'OFFICIAL_RAW' "
        "AND s.status = 'APPROVED'"
    )
    op.execute("DROP TRIGGER legal_documents_immutable ON legal_documents")
    op.execute("DROP FUNCTION prevent_legal_document_mutation()")
    op.execute("DROP TRIGGER legal_sources_protect_identity ON legal_sources")
    op.execute("DROP FUNCTION protect_legal_source_identity()")
    op.execute("DROP TRIGGER legal_fragments_append_only ON legal_fragments")
    op.execute("DROP FUNCTION guard_legal_fragment_mutation()")
    op.execute("DROP TRIGGER legal_versions_protect_content ON legal_versions")
    op.execute("DROP FUNCTION protect_legal_version_immutable_fields()")
    op.execute(
        "UPDATE legal_versions SET raw_mime_type = "
        "'application/vnd.dental-legal.official-excerpt+json' "
        "WHERE artifact_kind = 'NORMALIZED_EXCERPT' AND raw_mime_type = "
        "'application/vnd.dental-legal.normalized-excerpt+json'"
    )
    op.drop_constraint(
        "ck_legal_approval_events_regression_sha256",
        "legal_approval_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_legal_approval_events_policy_version",
        "legal_approval_events",
        type_="check",
    )
    op.drop_column("legal_approval_events", "regression_checks_json")
    op.drop_column("legal_approval_events", "regression_result_sha256")
    op.drop_column("legal_approval_events", "policy_version")
    op.drop_constraint("ck_legal_versions_official_metadata", "legal_versions", type_="check")
    op.drop_constraint(
        "ck_legal_versions_normalization_scope", "legal_versions", type_="check"
    )
    op.drop_constraint("ck_legal_versions_normalized_sha256", "legal_versions", type_="check")
    op.drop_constraint("ck_legal_versions_raw_size", "legal_versions", type_="check")
    op.drop_column("legal_versions", "normalization_scope")
    op.drop_column("legal_versions", "fragments_sha256")
    op.drop_column("legal_versions", "normalized_sha256")
    op.drop_column("legal_versions", "artifact_page_count")
    op.drop_column("legal_versions", "artifact_retrieved_at")
    op.drop_column("legal_versions", "raw_size_bytes")
