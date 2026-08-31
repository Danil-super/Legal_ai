"""add tenant-isolated clinic documents with explicit approval events

Revision ID: d3e6b9a14f20
Revises: c2f4a7d91e63
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d3e6b9a14f20"
down_revision: str | None = "c2f4a7d91e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_tenant_rls(table_name: str) -> None:
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "tenant_isolation_{table_name}" ON "{table_name}" '
        "USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid) "
        "WITH CHECK (clinic_id = "
        "nullif(current_setting('app.current_clinic_id', true), '')::uuid)"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE clinic_documents (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL,
            document_key varchar(100) NOT NULL,
            document_type varchar(80) NOT NULL,
            title varchar(240) NOT NULL,
            created_by_membership_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
            CONSTRAINT uq_clinic_documents_tenant_id UNIQUE (clinic_id, id),
            CONSTRAINT uq_clinic_documents_tenant_key UNIQUE (clinic_id, document_key),
            CONSTRAINT fk_clinic_documents_clinic
                FOREIGN KEY (clinic_id) REFERENCES clinics(id) ON DELETE RESTRICT,
            CONSTRAINT fk_clinic_documents_creator
                FOREIGN KEY (clinic_id, created_by_membership_id)
                REFERENCES clinic_users(clinic_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_clinic_documents_key
                CHECK (document_key ~ '^[a-z0-9][a-z0-9._-]{2,99}$'),
            CONSTRAINT ck_clinic_documents_type
                CHECK (document_type ~ '^[A-Z0-9_]{3,80}$'),
            CONSTRAINT ck_clinic_documents_title
                CHECK (char_length(btrim(title)) BETWEEN 1 AND 240)
        )
        """
    )
    op.create_index(
        "ix_clinic_documents_tenant_type",
        "clinic_documents",
        ["clinic_id", "document_type", "created_at", "id"],
    )

    op.execute(
        """
        CREATE TABLE clinic_document_versions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL,
            document_id uuid NOT NULL,
            version_no integer NOT NULL,
            source_filename varchar(255) NOT NULL,
            mime_type varchar(100) NOT NULL,
            raw_object_key varchar(500) NOT NULL,
            raw_sha256 varchar(64) NOT NULL,
            normalized_text text NOT NULL,
            normalized_text_sha256 varchar(64) NOT NULL,
            valid_from date NULL,
            valid_to date NULL,
            created_by_membership_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
            CONSTRAINT uq_clinic_document_versions_tenant_id UNIQUE (clinic_id, id),
            CONSTRAINT uq_clinic_document_versions_number
                UNIQUE (clinic_id, document_id, version_no),
            CONSTRAINT uq_clinic_document_versions_raw
                UNIQUE (clinic_id, document_id, raw_sha256),
            CONSTRAINT fk_clinic_document_versions_document
                FOREIGN KEY (clinic_id, document_id)
                REFERENCES clinic_documents(clinic_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_clinic_document_versions_creator
                FOREIGN KEY (clinic_id, created_by_membership_id)
                REFERENCES clinic_users(clinic_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_clinic_document_versions_number CHECK (version_no > 0),
            CONSTRAINT ck_clinic_document_versions_raw_sha
                CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_clinic_document_versions_text_sha
                CHECK (normalized_text_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_clinic_document_versions_mime
                CHECK (mime_type IN (
                    'application/pdf',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'application/rtf',
                    'text/plain'
                )),
            CONSTRAINT ck_clinic_document_versions_filename
                CHECK (char_length(btrim(source_filename)) BETWEEN 1 AND 255),
            CONSTRAINT ck_clinic_document_versions_object_key
                CHECK (char_length(btrim(raw_object_key)) BETWEEN 1 AND 500),
            CONSTRAINT ck_clinic_document_versions_text
                CHECK (char_length(normalized_text) > 0),
            CONSTRAINT ck_clinic_document_versions_dates
                CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
        )
        """
    )
    op.create_index(
        "ix_clinic_document_versions_tenant_document",
        "clinic_document_versions",
        ["clinic_id", "document_id", "version_no"],
    )

    op.execute(
        """
        CREATE TABLE clinic_document_fragments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL,
            version_id uuid NOT NULL,
            ordinal integer NOT NULL,
            heading varchar(300) NULL,
            structural_path varchar(500) NOT NULL,
            fragment_text text NOT NULL,
            text_sha256 varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
            CONSTRAINT uq_clinic_document_fragments_tenant_id UNIQUE (clinic_id, id),
            CONSTRAINT uq_clinic_document_fragments_ordinal
                UNIQUE (clinic_id, version_id, ordinal),
            CONSTRAINT fk_clinic_document_fragments_version
                FOREIGN KEY (clinic_id, version_id)
                REFERENCES clinic_document_versions(clinic_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_clinic_document_fragments_ordinal CHECK (ordinal > 0),
            CONSTRAINT ck_clinic_document_fragments_path
                CHECK (char_length(btrim(structural_path)) BETWEEN 1 AND 500),
            CONSTRAINT ck_clinic_document_fragments_text
                CHECK (char_length(fragment_text) > 0),
            CONSTRAINT ck_clinic_document_fragments_sha
                CHECK (text_sha256 ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.create_index(
        "ix_clinic_document_fragments_tenant_version",
        "clinic_document_fragments",
        ["clinic_id", "version_id", "ordinal"],
    )

    op.execute(
        """
        CREATE TABLE clinic_document_approval_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL,
            version_id uuid NOT NULL,
            actor_membership_id uuid NOT NULL,
            decision varchar(20) NOT NULL,
            reason_code varchar(80) NOT NULL,
            expected_raw_sha256 varchar(64) NOT NULL,
            expected_normalized_text_sha256 varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
            CONSTRAINT fk_clinic_document_approval_events_version
                FOREIGN KEY (clinic_id, version_id)
                REFERENCES clinic_document_versions(clinic_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_clinic_document_approval_events_actor
                FOREIGN KEY (clinic_id, actor_membership_id)
                REFERENCES clinic_users(clinic_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_clinic_document_approval_events_decision
                CHECK (decision IN ('APPROVED', 'RETIRED', 'BLOCKED')),
            CONSTRAINT ck_clinic_document_approval_events_reason
                CHECK (reason_code ~ '^[A-Z0-9_]{3,80}$'),
            CONSTRAINT ck_clinic_document_approval_events_raw_sha
                CHECK (expected_raw_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_clinic_document_approval_events_text_sha
                CHECK (expected_normalized_text_sha256 ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.create_index(
        "ix_clinic_document_approval_events_version_time",
        "clinic_document_approval_events",
        ["clinic_id", "version_id", "created_at", "id"],
    )

    op.execute(
        """
        CREATE FUNCTION validate_clinic_document_approval_event()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            membership_role varchar(30);
            membership_status varchar(20);
            current_raw_sha varchar(64);
            current_text_sha varchar(64);
        BEGIN
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
    op.execute(
        """
        CREATE TRIGGER clinic_document_approval_events_validate
        BEFORE INSERT ON clinic_document_approval_events
        FOR EACH ROW EXECUTE FUNCTION validate_clinic_document_approval_event()
        """
    )

    for table_name in (
        "clinic_documents",
        "clinic_document_versions",
        "clinic_document_fragments",
        "clinic_document_approval_events",
    ):
        _enable_tenant_rls(table_name)
        op.execute(
            f'CREATE TRIGGER {table_name}_append_only BEFORE UPDATE OR DELETE ON "{table_name}" '
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )

    op.execute(
        """
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
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS approved_clinic_document_fragments")
    for table_name in (
        "clinic_document_approval_events",
        "clinic_document_fragments",
        "clinic_document_versions",
        "clinic_documents",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_append_only ON {table_name}")
    op.execute(
        "DROP TRIGGER IF EXISTS clinic_document_approval_events_validate "
        "ON clinic_document_approval_events"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_clinic_document_approval_event()")
    op.drop_table("clinic_document_approval_events")
    op.drop_table("clinic_document_fragments")
    op.drop_table("clinic_document_versions")
    op.drop_table("clinic_documents")
