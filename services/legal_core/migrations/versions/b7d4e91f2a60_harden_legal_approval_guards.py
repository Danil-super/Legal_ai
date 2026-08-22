"""harden legal approval guards

Revision ID: b7d4e91f2a60
Revises: a13c6d28e904
Create Date: 2026-08-22 19:15:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d4e91f2a60"
down_revision: str | None = "a13c6d28e904"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION legal_canonical_jsonb(payload jsonb)
        RETURNS text AS $$
        DECLARE
          canonical text;
        BEGIN
          CASE jsonb_typeof(payload)
            WHEN 'object' THEN
              SELECT '{' || coalesce(string_agg(
                       to_jsonb(item_key)::text || ':' || legal_canonical_jsonb(item_value),
                       ',' ORDER BY item_key COLLATE "C"), '') || '}'
                INTO canonical
                FROM jsonb_each(payload) AS items(item_key, item_value);
            WHEN 'array' THEN
              SELECT '[' || coalesce(string_agg(
                       legal_canonical_jsonb(item_value), ',' ORDER BY ordinal), '') || ']'
                INTO canonical
                FROM jsonb_array_elements(payload) WITH ORDINALITY
                     AS items(item_value, ordinal);
            ELSE
              canonical := payload::text;
          END CASE;
          RETURN canonical;
        END;
        $$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
        """
    )
    op.execute(
        """
        CREATE FUNCTION legal_regression_result_sha256(payload jsonb)
        RETURNS text AS $$
          SELECT encode(
            digest(convert_to(legal_canonical_jsonb(payload), 'UTF8'), 'sha256'),
            'hex'
          )
        $$ LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        """
    )
    op.execute(
        """
        CREATE FUNCTION legal_approval_event_is_current(
          approval_event legal_approval_events
        )
        RETURNS boolean AS $$
          SELECT EXISTS (
            SELECT 1
              FROM legal_versions v
              JOIN legal_sources s ON s.id = v.source_id
              JOIN legal_documents d ON d.id = v.document_id
              JOIN users u ON u.id = (approval_event).actor_user_id
             WHERE v.id = (approval_event).legal_version_id
               AND (approval_event).decision = 'APPROVED'
               AND (approval_event).reason_code = 'HUMAN_LEGAL_REVIEW_PASSED'
               AND (approval_event).expected_sha256 = v.raw_sha256
               AND (approval_event).policy_version = 'dental-legal-approval.v1'
               AND (approval_event).regression_result_sha256 =
                   legal_regression_result_sha256(
                     (approval_event).regression_checks_json
                   )
               AND u.status = 'ACTIVE'
               AND u.system_role = 'LEGAL_EDITOR'
               AND s.status IN ('DRAFT', 'APPROVED')
               AND v.artifact_kind = 'OFFICIAL_RAW'
               AND encode(digest(v.raw_bytes, 'sha256'), 'hex') = v.raw_sha256
               AND octet_length(v.raw_bytes) = v.raw_size_bytes
               AND encode(
                     digest(convert_to(v.normalized_text, 'UTF8'), 'sha256'), 'hex'
                   ) = v.normalized_sha256
               AND v.artifact_retrieved_at IS NOT NULL
               AND v.normalization_scope = 'FULL_DOCUMENT'
               AND (v.raw_mime_type <> 'application/pdf'
                    OR (v.artifact_page_count IS NOT NULL
                        AND substring(v.raw_bytes FROM 1 FOR 5) = convert_to('%PDF-', 'UTF8')))
               AND v.source_url ~ '^https://[^/:?#]+'
               AND s.allowed_hosts @> jsonb_build_array(
                     lower(substring(v.source_url FROM '^https://([^/:?#]+)'))
                   )
               AND (v.effective_to IS NULL OR v.effective_to > v.effective_from)
               AND (
                     d.official_number IS NULL
                     OR d.official_number NOT IN ('736', '659')
                     OR (d.official_number = '736'
                         AND v.effective_from = DATE '2023-09-01'
                         AND v.effective_to = DATE '2026-09-01')
                     OR (d.official_number = '659'
                         AND v.effective_from = DATE '2026-09-01'
                         AND v.effective_to = DATE '2031-09-01')
                   )
               AND (approval_event).checks_json @> jsonb_build_object(
                     'sourceIsOfficial', true,
                     'artifactIsComplete', true,
                     'effectiveDatesVerified', true,
                     'fragmentsVerified', true,
                     'expectedNormalizedSha256', v.normalized_sha256,
                     'expectedFragmentsSha256', v.fragments_sha256,
                     'expectedEffectiveFrom', v.effective_from::text
                   )
               AND (approval_event).checks_json ? 'expectedEffectiveTo'
               AND ((approval_event).checks_json ->> 'expectedEffectiveTo')
                   IS NOT DISTINCT FROM v.effective_to::text
               AND (approval_event).regression_checks_json @> jsonb_build_object(
                     'policyVersion', (approval_event).policy_version,
                     'passed', true,
                     'rawShaMatches', true,
                     'rawSizeMatches', true,
                     'normalizedShaMatches', true,
                     'fragmentsSha256', v.fragments_sha256,
                     'normalizationScope', v.normalization_scope,
                     'effectiveFrom', v.effective_from::text,
                     'effectiveRangeValid', true
                   )
               AND (approval_event).regression_checks_json ? 'effectiveTo'
               AND ((approval_event).regression_checks_json ->> 'effectiveTo')
                   IS NOT DISTINCT FROM v.effective_to::text
               AND ((approval_event).regression_checks_json ->> 'fragmentCount')::integer =
                   (SELECT count(*) FROM legal_fragments f WHERE f.version_id = v.id)
               AND EXISTS (SELECT 1 FROM legal_fragments f WHERE f.version_id = v.id)
               AND NOT EXISTS (
                     SELECT 1
                       FROM legal_fragments f
                      WHERE f.version_id = v.id
                        AND (
                          encode(
                            digest(convert_to(f.fragment_text, 'UTF8'), 'sha256'), 'hex'
                          ) <> f.text_sha256
                          OR position(f.fragment_text IN v.normalized_text) = 0
                        )
                   )
               AND v.fragments_sha256 = (
                     SELECT encode(digest(convert_to(coalesce(string_agg(
                              f.ordinal::text || ':' || encode(digest(
                                convert_to(f.fragment_text, 'UTF8'), 'sha256'
                              ), 'hex'), E'\n' ORDER BY f.ordinal), ''), 'UTF8'),
                              'sha256'), 'hex')
                       FROM legal_fragments f
                      WHERE f.version_id = v.id
                   )
          )
        $$ LANGUAGE sql STABLE
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM legal_sources
             WHERE status NOT IN ('DRAFT', 'APPROVED')
                OR (status = 'DRAFT' AND (approved_by IS NOT NULL OR approved_at IS NOT NULL))
                OR (status = 'APPROVED' AND (approved_by IS NULL OR approved_at IS NULL))
          ) THEN
            RAISE EXCEPTION 'preflight failed: invalid legal source lifecycle state';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM legal_approval_events e
              JOIN legal_versions v ON v.id = e.legal_version_id
             WHERE e.decision = 'APPROVED'
               AND (v.approval_state <> 'APPROVED'
                    OR NOT legal_approval_event_is_current(e))
          ) THEN
            RAISE EXCEPTION 'preflight failed: invalid committed legal approval event';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM legal_versions v
             WHERE v.approval_state = 'APPROVED'
               AND NOT EXISTS (
                 SELECT 1 FROM legal_approval_events e
                  WHERE e.legal_version_id = v.id
                    AND e.actor_user_id = v.approved_by
                    AND legal_approval_event_is_current(e)
               )
          ) THEN
            RAISE EXCEPTION 'preflight failed: approved version lacks current approval evidence';
          END IF;
        END $$
        """
    )
    op.create_check_constraint(
        "ck_legal_sources_lifecycle",
        "legal_sources",
        "(status = 'DRAFT' AND approved_by IS NULL AND approved_at IS NULL) OR "
        "(status = 'APPROVED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
    )
    op.execute(
        """
        CREATE FUNCTION validate_legal_approval_event_insert()
        RETURNS trigger AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(
            hashtextextended(NEW.legal_version_id::text, 736659)
          );
          IF NOT EXISTS (
            SELECT 1 FROM users u
             WHERE u.id = NEW.actor_user_id
               AND u.status = 'ACTIVE'
               AND u.system_role = 'LEGAL_EDITOR'
          ) THEN
            RAISE EXCEPTION 'legal approval event requires active LEGAL_EDITOR';
          END IF;
          IF NEW.regression_result_sha256 <>
             legal_regression_result_sha256(NEW.regression_checks_json) THEN
            RAISE EXCEPTION 'legal approval event regression digest mismatch';
          END IF;
          IF NEW.decision = 'APPROVED' THEN
            IF NOT EXISTS (
              SELECT 1 FROM legal_versions v
               WHERE v.id = NEW.legal_version_id
                 AND v.approval_state = 'REVIEW_REQUIRED'
            ) OR NOT legal_approval_event_is_current(NEW) THEN
              RAISE EXCEPTION 'legal approval event integrity validation failed';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER legal_approval_events_validate_insert
        BEFORE INSERT ON legal_approval_events
        FOR EACH ROW EXECUTE FUNCTION validate_legal_approval_event_insert()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_legal_fragment_mutation()
        RETURNS trigger AS $$
        DECLARE
          parent_state text;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            SELECT approval_state INTO parent_state
              FROM legal_versions
             WHERE id = NEW.version_id
             FOR KEY SHARE;
            IF parent_state IS NULL THEN
              RAISE EXCEPTION 'legal fragment parent version does not exist';
            END IF;
            PERFORM pg_advisory_xact_lock(
              hashtextextended(NEW.version_id::text, 736659)
            );
            SELECT approval_state INTO parent_state
              FROM legal_versions
             WHERE id = NEW.version_id;
            IF parent_state <> 'REVIEW_REQUIRED' THEN
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
        CREATE OR REPLACE FUNCTION protect_legal_version_immutable_fields()
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
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.id::text, 736659));
            IF NEW.approved_by IS NULL OR NEW.approved_at IS NULL
               OR NOT NEW.regression_passed
               OR NOT EXISTS (
                 SELECT 1
                   FROM legal_approval_events e
                  WHERE e.legal_version_id = NEW.id
                    AND e.actor_user_id = NEW.approved_by
                    AND legal_approval_event_is_current(e)
               ) THEN
              RAISE EXCEPTION 'approved legal version requires current approval event';
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
        CREATE OR REPLACE FUNCTION protect_legal_source_identity()
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
          IF OLD.status = 'DRAFT' AND NEW.status = 'APPROVED' THEN
            IF NEW.approved_by IS NULL OR NEW.approved_at IS NULL
               OR NOT EXISTS (
                 SELECT 1
                   FROM legal_approval_events e
                   JOIN legal_versions v ON v.id = e.legal_version_id
                  WHERE v.source_id = OLD.id
                    AND e.actor_user_id = NEW.approved_by
                    AND v.approval_state = 'APPROVED'
                    AND v.approved_by = e.actor_user_id
                    AND legal_approval_event_is_current(e)
               ) THEN
              RAISE EXCEPTION 'legal source approval transition requires current approval event';
            END IF;
          ELSIF OLD.status IS DISTINCT FROM NEW.status
             OR OLD.approved_by IS DISTINCT FROM NEW.approved_by
             OR OLD.approved_at IS DISTINCT FROM NEW.approved_at THEN
            RAISE EXCEPTION 'legal source lifecycle transition is not allowed';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
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
        "AND ae.actor_user_id = v.approved_by "
        "AND legal_approval_event_is_current(ae) "
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
        "JOIN legal_approval_events ae ON ae.legal_version_id = v.id "
        "AND ae.decision = 'APPROVED' AND ae.actor_user_id = v.approved_by "
        "AND ae.expected_sha256 = v.raw_sha256 "
        "AND ae.policy_version = 'dental-legal-approval.v1' "
        "AND ae.regression_checks_json ->> 'passed' = 'true' "
        "WHERE v.approval_state = 'APPROVED' AND v.artifact_kind = 'OFFICIAL_RAW' "
        "AND s.status = 'APPROVED'"
    )
    op.execute("DROP TRIGGER legal_approval_events_validate_insert ON legal_approval_events")
    op.execute("DROP FUNCTION validate_legal_approval_event_insert()")
    op.drop_constraint("ck_legal_sources_lifecycle", "legal_sources", type_="check")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_legal_fragment_mutation()
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
        CREATE OR REPLACE FUNCTION protect_legal_source_identity()
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
        CREATE OR REPLACE FUNCTION protect_legal_version_immutable_fields()
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
                 SELECT 1 FROM legal_approval_events e
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
    op.execute("DROP FUNCTION legal_approval_event_is_current(legal_approval_events)")
    op.execute("DROP FUNCTION legal_regression_result_sha256(jsonb)")
    op.execute("DROP FUNCTION legal_canonical_jsonb(jsonb)")
