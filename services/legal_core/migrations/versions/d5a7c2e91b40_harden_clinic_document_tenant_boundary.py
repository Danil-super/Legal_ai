"""harden clinic document tenant boundary independently of PostgreSQL role privileges

Revision ID: d5a7c2e91b40
Revises: d3e6b9a14f20
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d5a7c2e91b40"
down_revision: str | None = "d3e6b9a14f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEW_SQL = """
CREATE VIEW approved_clinic_document_fragments
WITH (security_invoker = true)
AS
SELECT
    f.clinic_id,
    f.id AS fragment_id,
    f.version_id,
    v.document_id,
    d.document_key,
    d.document_type,
    d.title AS document_title,
    v.version_no,
    v.valid_from,
    v.valid_to,
    f.ordinal,
    f.heading,
    f.structural_path,
    f.fragment_text,
    f.text_sha256,
    v.raw_sha256,
    v.normalized_text_sha256
FROM clinic_document_fragments AS f
JOIN clinic_document_versions AS v
  ON v.clinic_id = f.clinic_id
 AND v.id = f.version_id
JOIN clinic_documents AS d
  ON d.clinic_id = v.clinic_id
 AND d.id = v.document_id
JOIN LATERAL (
    SELECT e.decision
      FROM clinic_document_approval_events AS e
     WHERE e.clinic_id = v.clinic_id
       AND e.version_id = v.id
     ORDER BY e.created_at DESC, e.id DESC
     LIMIT 1
) AS latest_review ON latest_review.decision = 'APPROVED'
WHERE f.clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid
"""

_PREVIOUS_VIEW_SQL = """
CREATE VIEW approved_clinic_document_fragments
WITH (security_invoker = true)
AS
SELECT
    f.clinic_id,
    f.id AS fragment_id,
    f.version_id,
    v.document_id,
    d.document_key,
    d.document_type,
    d.title AS document_title,
    v.version_no,
    v.valid_from,
    v.valid_to,
    f.ordinal,
    f.heading,
    f.structural_path,
    f.fragment_text,
    f.text_sha256,
    v.raw_sha256,
    v.normalized_text_sha256
FROM clinic_document_fragments AS f
JOIN clinic_document_versions AS v
  ON v.clinic_id = f.clinic_id
 AND v.id = f.version_id
JOIN clinic_documents AS d
  ON d.clinic_id = v.clinic_id
 AND d.id = v.document_id
JOIN LATERAL (
    SELECT e.decision
      FROM clinic_document_approval_events AS e
     WHERE e.clinic_id = v.clinic_id
       AND e.version_id = v.id
     ORDER BY e.created_at DESC, e.id DESC
     LIMIT 1
) AS latest_review ON latest_review.decision = 'APPROVED'
"""


def _replace_validation_function(*, enforce_context: bool) -> None:
    context_guard = """
            IF NEW.clinic_id IS DISTINCT FROM
               nullif(current_setting('app.current_clinic_id', true), '')::uuid THEN
                RAISE EXCEPTION 'clinic document approval tenant context mismatch';
            END IF;
""" if enforce_context else ""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION validate_clinic_document_approval_event()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            membership_role varchar(30);
            membership_status varchar(20);
            current_raw_sha varchar(64);
            current_text_sha varchar(64);
        BEGIN
{context_guard}
            SELECT role, status
              INTO membership_role, membership_status
              FROM clinic_users
             WHERE clinic_id = NEW.clinic_id
               AND id = NEW.actor_membership_id;

            IF membership_status IS DISTINCT FROM 'ACTIVE'
               OR membership_role NOT IN ('CLINIC_ADMIN', 'CLINIC_MANAGER', 'CLINIC_OWNER') THEN
                RAISE EXCEPTION 'active clinic administrator role is required';
            END IF;

            SELECT raw_sha256, normalized_text_sha256
              INTO current_raw_sha, current_text_sha
              FROM clinic_document_versions
             WHERE clinic_id = NEW.clinic_id
               AND id = NEW.version_id;

            IF current_raw_sha IS NULL OR current_text_sha IS NULL THEN
                RAISE EXCEPTION 'clinic document version is not available in tenant';
            END IF;
            IF current_raw_sha <> NEW.expected_raw_sha256
               OR current_text_sha <> NEW.expected_normalized_text_sha256 THEN
                RAISE EXCEPTION 'clinic document approval hash mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def upgrade() -> None:
    op.execute("DROP VIEW approved_clinic_document_fragments")
    op.execute(_VIEW_SQL)
    _replace_validation_function(enforce_context=True)


def downgrade() -> None:
    _replace_validation_function(enforce_context=False)
    op.execute("DROP VIEW approved_clinic_document_fragments")
    op.execute(_PREVIOUS_VIEW_SQL)
