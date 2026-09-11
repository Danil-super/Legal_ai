"""allow reviewed ConsultantPlus legal copies

Revision ID: c8e5f1a2b6d4
Revises: d0e1f2a3b4c5
Create Date: 2026-09-11 16:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8e5f1a2b6d4"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _approval_event_current_function() -> str:
    """Return the DB-side counterpart of Legal Core's checksum-bound approval guard."""

    return r"""
        CREATE OR REPLACE FUNCTION legal_approval_event_is_current(
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
               AND (approval_event).regression_result_sha256 =
                   legal_regression_result_sha256(
                     (approval_event).regression_checks_json
                   )
               AND u.status = 'ACTIVE'
               AND u.system_role = 'LEGAL_EDITOR'
               AND s.status IN ('DRAFT', 'APPROVED')
               AND v.artifact_kind IN ('OFFICIAL_RAW', 'THIRD_PARTY_VERIFIED_COPY')
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
               AND (
                     ((approval_event).policy_version = 'dental-legal-approval.v1'
                      AND v.artifact_kind = 'OFFICIAL_RAW'
                      AND s.trust_level = 'PRIMARY'
                      AND (approval_event).checks_json @> jsonb_build_object(
                            'sourceIsOfficial', true,
                            'artifactIsComplete', true,
                            'effectiveDatesVerified', true,
                            'fragmentsVerified', true,
                            'expectedNormalizedSha256', v.normalized_sha256,
                            'expectedFragmentsSha256', v.fragments_sha256,
                            'expectedEffectiveFrom', v.effective_from::text
                          ))
                     OR ((approval_event).policy_version = 'dental-legal-approval.v2'
                         AND (approval_event).checks_json @> jsonb_build_object(
                               'artifactIsComplete', true,
                               'effectiveDatesVerified', true,
                               'fragmentsVerified', true,
                               'expectedNormalizedSha256', v.normalized_sha256,
                               'expectedFragmentsSha256', v.fragments_sha256,
                               'expectedEffectiveFrom', v.effective_from::text
                             )
                         AND (
                               (v.artifact_kind = 'OFFICIAL_RAW'
                                AND s.trust_level = 'PRIMARY'
                                AND (approval_event).checks_json @> jsonb_build_object(
                                      'sourceIsOfficial', true,
                                      'officialTextCompared', true
                                    ))
                               OR (v.artifact_kind = 'THIRD_PARTY_VERIFIED_COPY'
                                   AND s.trust_level = 'VERIFIED_COPY'
                                   AND (approval_event).checks_json @> jsonb_build_object(
                                         'sourceIsOfficial', false,
                                         'officialTextCompared', true
                                       ))
                             ))
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


def upgrade() -> None:
    op.drop_constraint("ck_legal_versions_official_metadata", "legal_versions", type_="check")
    op.drop_constraint(
        "ck_legal_versions_approved_official_raw", "legal_versions", type_="check"
    )
    op.drop_constraint("ck_legal_versions_artifact_kind", "legal_versions", type_="check")
    op.create_check_constraint(
        "ck_legal_versions_artifact_kind",
        "legal_versions",
        "artifact_kind IN "
        "('NORMALIZED_EXCERPT', 'OFFICIAL_RAW', 'THIRD_PARTY_VERIFIED_COPY')",
    )
    op.create_check_constraint(
        "ck_legal_versions_approved_reviewable_artifact",
        "legal_versions",
        "approval_state <> 'APPROVED' OR "
        "artifact_kind IN ('OFFICIAL_RAW', 'THIRD_PARTY_VERIFIED_COPY')",
    )
    op.create_check_constraint(
        "ck_legal_versions_reviewable_metadata",
        "legal_versions",
        "artifact_kind NOT IN ('OFFICIAL_RAW', 'THIRD_PARTY_VERIFIED_COPY') OR "
        "(artifact_retrieved_at IS NOT NULL AND normalization_scope = 'FULL_DOCUMENT' "
        "AND (raw_mime_type <> 'application/pdf' OR artifact_page_count IS NOT NULL))",
    )
    op.execute(_approval_event_current_function())


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM legal_versions
             WHERE artifact_kind = 'THIRD_PARTY_VERIFIED_COPY'
            UNION ALL
            SELECT 1 FROM legal_approval_events
             WHERE policy_version = 'dental-legal-approval.v2'
          ) THEN
            RAISE EXCEPTION
              'cannot downgrade while v2 legal evidence is retained';
          END IF;
        END $$
        """
    )
    op.drop_constraint(
        "ck_legal_versions_reviewable_metadata", "legal_versions", type_="check"
    )
    op.drop_constraint(
        "ck_legal_versions_approved_reviewable_artifact", "legal_versions", type_="check"
    )
    op.drop_constraint("ck_legal_versions_artifact_kind", "legal_versions", type_="check")
    op.create_check_constraint(
        "ck_legal_versions_artifact_kind",
        "legal_versions",
        "artifact_kind IN ('NORMALIZED_EXCERPT', 'OFFICIAL_RAW')",
    )
    op.create_check_constraint(
        "ck_legal_versions_approved_official_raw",
        "legal_versions",
        "approval_state <> 'APPROVED' OR artifact_kind = 'OFFICIAL_RAW'",
    )
    op.create_check_constraint(
        "ck_legal_versions_official_metadata",
        "legal_versions",
        "artifact_kind <> 'OFFICIAL_RAW' OR "
        "(artifact_retrieved_at IS NOT NULL AND normalization_scope = 'FULL_DOCUMENT' "
        "AND (raw_mime_type <> 'application/pdf' OR artifact_page_count IS NOT NULL))",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION legal_approval_event_is_current(
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
