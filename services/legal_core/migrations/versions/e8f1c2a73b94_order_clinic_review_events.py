"""make clinic document review event ordering deterministic

Revision ID: e8f1c2a73b94
Revises: d5a7c2e91b40
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e8f1c2a73b94"
down_revision: str | None = "d5a7c2e91b40"
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
     ORDER BY e.event_seq DESC
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
WHERE f.clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid
"""


def upgrade() -> None:
    op.execute("DROP VIEW approved_clinic_document_fragments")
    op.execute("ALTER TABLE clinic_document_approval_events ADD COLUMN event_seq bigint")

    # Existing append-only rows cannot be semantically reordered. Preserve the previous
    # `(created_at, id)` ordering during the one-time backfill, then use a database-owned
    # monotonic identity for every new event.
    op.execute(
        "ALTER TABLE clinic_document_approval_events "
        "DISABLE TRIGGER clinic_document_approval_events_append_only"
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS event_seq
            FROM clinic_document_approval_events
        )
        UPDATE clinic_document_approval_events AS event
           SET event_seq = ranked.event_seq
          FROM ranked
         WHERE event.id = ranked.id
        """
    )
    op.execute(
        "ALTER TABLE clinic_document_approval_events "
        "ENABLE TRIGGER clinic_document_approval_events_append_only"
    )
    op.execute(
        "ALTER TABLE clinic_document_approval_events "
        "ALTER COLUMN event_seq SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE clinic_document_approval_events "
        "ALTER COLUMN event_seq ADD GENERATED ALWAYS AS IDENTITY"
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence(
                'clinic_document_approval_events',
                'event_seq'
            )::regclass,
            COALESCE(max(event_seq), 1),
            max(event_seq) IS NOT NULL
        )
        FROM clinic_document_approval_events
        """
    )
    op.create_index(
        "uq_clinic_document_approval_events_event_seq",
        "clinic_document_approval_events",
        ["event_seq"],
        unique=True,
    )
    op.create_index(
        "ix_clinic_document_approval_events_version_seq",
        "clinic_document_approval_events",
        ["clinic_id", "version_id", "event_seq"],
    )
    op.execute(_VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW approved_clinic_document_fragments")
    op.drop_index(
        "ix_clinic_document_approval_events_version_seq",
        table_name="clinic_document_approval_events",
    )
    op.drop_index(
        "uq_clinic_document_approval_events_event_seq",
        table_name="clinic_document_approval_events",
    )
    op.execute(
        "ALTER TABLE clinic_document_approval_events "
        "ALTER COLUMN event_seq DROP IDENTITY IF EXISTS"
    )
    op.drop_column("clinic_document_approval_events", "event_seq")
    op.execute(_PREVIOUS_VIEW_SQL)
