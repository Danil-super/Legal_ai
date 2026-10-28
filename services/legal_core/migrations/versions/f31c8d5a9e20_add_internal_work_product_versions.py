"""Add immutable internal work-product versions without enabling a producer.

Revision ID: f31c8d5a9e20
Revises: e21b7c4d8a90
"""

import sqlalchemy as sa
from alembic import op

revision = "f31c8d5a9e20"
down_revision = "e21b7c4d8a90"
branch_labels = None
depends_on = None

_TABLE_SQL = """
CREATE TABLE public.case_work_product_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id uuid NOT NULL,
    case_id uuid NOT NULL,
    work_product_id uuid NOT NULL,
    version integer NOT NULL,
    parent_version integer,
    origin varchar(10) NOT NULL,
    review_state varchar(20) NOT NULL,
    author_membership_id uuid NOT NULL,
    reviewed_by_membership_id uuid,
    reviewed_at timestamptz,
    schema_version varchar(80) NOT NULL,
    payload_json jsonb NOT NULL,
    content_sha256 varchar(64) NOT NULL,
    source_operation_id uuid NOT NULL,
    fact_snapshot_sha256 varchar(64) NOT NULL,
    evidence_trace_sha256 varchar(64),
    clinic_document_context_trace_sha256 varchar(64),
    reference_example_trace_sha256 varchar(64),
    risk_policy_version varchar(80) NOT NULL,
    as_of_date date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
    CONSTRAINT uq_work_product_case_version
        UNIQUE (clinic_id, case_id, work_product_id, version),
    FOREIGN KEY (clinic_id, case_id) REFERENCES public.cases(clinic_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (clinic_id, author_membership_id)
        REFERENCES public.clinic_users(clinic_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (clinic_id, reviewed_by_membership_id)
        REFERENCES public.clinic_users(clinic_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_work_product_parent_version
        FOREIGN KEY (clinic_id, case_id, work_product_id, parent_version)
        REFERENCES public.case_work_product_versions(clinic_id, case_id, work_product_id, version)
        ON DELETE RESTRICT,
    CONSTRAINT ck_work_product_parent CHECK (
        version > 0 AND ((version = 1 AND parent_version IS NULL) OR
        (version > 1 AND parent_version IS NOT NULL AND parent_version=version-1))
    ),
    CONSTRAINT ck_work_product_schema CHECK (schema_version = 'ai-first-work-product.v1'),
    CONSTRAINT ck_work_product_review_metadata CHECK (
        origin IN ('AI','HUMAN') AND (version > 1 OR origin = 'AI') AND (
        (origin='AI' AND review_state='UNAPPROVED'
         AND reviewed_by_membership_id IS NULL AND reviewed_at IS NULL) OR
        (origin='HUMAN' AND review_state='HUMAN_REVISED'
         AND reviewed_by_membership_id IS NULL AND reviewed_at IS NULL) OR
        (origin='HUMAN' AND review_state='HUMAN_APPROVED'
         AND reviewed_by_membership_id=author_membership_id
         AND reviewed_by_membership_id IS NOT NULL AND reviewed_at IS NOT NULL))
    ),
    CONSTRAINT ck_work_product_payload_budget CHECK (
        jsonb_typeof(payload_json)='object' AND octet_length(payload_json::text) <= 65536
    ),
    CONSTRAINT ck_work_product_payload_binding CHECK ((
        payload_json->>'schemaVersion' = schema_version
        AND payload_json->>'kind' = 'LAWYER_REVIEW_DRAFT'
        AND payload_json->>'status' = 'UNAPPROVED'
        AND payload_json->>'workProductId' = work_product_id::text
        AND payload_json->'version' = to_jsonb(version)
        AND payload_json->'parentVersion' IS NOT DISTINCT FROM
            COALESCE(to_jsonb(parent_version), 'null'::jsonb)
        AND payload_json->>'clinicId' = clinic_id::text
        AND payload_json#>>'{source,caseId}' = case_id::text
        AND payload_json#>>'{source,operationId}' = source_operation_id::text
        AND payload_json#>>'{source,factSnapshotSha256}' = fact_snapshot_sha256
        AND payload_json->>'asOfDate' = as_of_date::text
        AND payload_json->>'riskPolicyVersion' = risk_policy_version
        AND CASE WHEN evidence_trace_sha256 IS NULL THEN
            payload_json#>'{source,context}' = 'null'::jsonb
            AND payload_json->'legalFragmentIds' = '[]'::jsonb
        ELSE
            payload_json#>>'{source,context,evidenceTraceSha256}' = evidence_trace_sha256
            AND payload_json#>>'{source,context,clinicDocumentContextTraceSha256}'
                = clinic_document_context_trace_sha256
            AND payload_json#>>'{source,context,asOfDate}' = as_of_date::text
            AND payload_json#>>'{source,context,riskPolicyVersion}' = risk_policy_version
        END
    ) IS TRUE),
    CONSTRAINT ck_work_product_content_hash CHECK (
        content_sha256 ~ '^[0-9a-f]{64}$' AND content_sha256 =
        encode(public.digest(convert_to(public.legal_canonical_jsonb(payload_json),
        'UTF8'), 'sha256'), 'hex')
    ),
    CONSTRAINT ck_work_product_snapshot_hashes CHECK (
        fact_snapshot_sha256 ~ '^[0-9a-f]{64}$' AND
        (evidence_trace_sha256 IS NULL OR evidence_trace_sha256 ~ '^[0-9a-f]{64}$') AND
        (clinic_document_context_trace_sha256 IS NULL OR
            clinic_document_context_trace_sha256 ~ '^[0-9a-f]{64}$') AND
        (reference_example_trace_sha256 IS NULL OR
            reference_example_trace_sha256 ~ '^[0-9a-f]{64}$') AND
        ((evidence_trace_sha256 IS NULL) = (clinic_document_context_trace_sha256 IS NULL))
    ),
    CONSTRAINT ck_work_product_policy CHECK (
        char_length(btrim(risk_policy_version)) BETWEEN 1 AND 80
    )
);
CREATE INDEX ix_work_product_case_versions
    ON public.case_work_product_versions(clinic_id, case_id, created_at, id);
ALTER TABLE public.case_work_product_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_work_product_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_work_product_versions ON public.case_work_product_versions
    USING (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid)
    WITH CHECK (clinic_id = nullif(current_setting('app.current_clinic_id', true), '')::uuid);
CREATE TRIGGER case_work_product_versions_immutable BEFORE UPDATE OR DELETE
    ON public.case_work_product_versions FOR EACH ROW
    EXECUTE FUNCTION public.prevent_immutable_mutation();
"""

_INSERT_GUARD_SQL = """
CREATE FUNCTION public.guard_case_work_product_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog, public AS $$
DECLARE parent_case record;
BEGIN
    -- Application writers must lock Case BEFORE issuing INSERT (table locks
    -- precede this trigger). This only rechecks content after any row-lock wait.
    -- Use wall-clock time, not the transaction start time.
    SELECT status, content_purged_at, retention_due_at INTO parent_case
      FROM public.cases WHERE clinic_id=NEW.clinic_id AND id=NEW.case_id FOR UPDATE;
    IF NOT FOUND OR parent_case.status='CONTENT_PURGED'
       OR parent_case.content_purged_at IS NOT NULL
       OR parent_case.retention_due_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'case content is not available for a work product' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.guard_case_work_product_insert() FROM PUBLIC;
CREATE TRIGGER case_work_product_versions_insert_guard BEFORE INSERT
    ON public.case_work_product_versions FOR EACH ROW
    EXECUTE FUNCTION public.guard_case_work_product_insert();
"""

_DISABLE_ANCHOR = "EXECUTE 'ALTER TABLE public.case_facts DISABLE TRIGGER case_facts_immutable';"
_ENABLE_ANCHOR = "EXECUTE 'ALTER TABLE public.case_facts ENABLE TRIGGER case_facts_immutable';"
_DELETE_ANCHOR = "DELETE FROM public.case_facts"
_DISABLE = ("EXECUTE 'ALTER TABLE public.case_work_product_versions "
            "DISABLE TRIGGER case_work_product_versions_immutable';\n            ")
_ENABLE = ("EXECUTE 'ALTER TABLE public.case_work_product_versions "
           "ENABLE TRIGGER case_work_product_versions_immutable';\n            ")
_DELETE = ("DELETE FROM public.case_work_product_versions "
           "WHERE clinic_id=target.clinic_id AND case_id=target.id;\n            ")


def _replace_retention(*, expanded: bool) -> None:
    definition = str(op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef('public.purge_expired_case_content()'::regprocedure)"
    )))
    if expanded:
        if (definition.count(_DISABLE_ANCHOR) != 1 or definition.count(_ENABLE_ANCHOR) != 2
                or definition.count(_DELETE_ANCHOR) != 1):
            raise RuntimeError("Unexpected retention function: "
                               "work-product cleanup was not installed")
        definition = definition.replace(_DISABLE_ANCHOR, _DISABLE + _DISABLE_ANCHOR)
        definition = definition.replace(_ENABLE_ANCHOR, _ENABLE + _ENABLE_ANCHOR)
        definition = definition.replace(_DELETE_ANCHOR, _DELETE + _DELETE_ANCHOR)
    else:
        for addition, count in ((_DISABLE, 1), (_ENABLE, 2), (_DELETE, 1)):
            if definition.count(addition) != count:
                raise RuntimeError("Unexpected retention function: "
                                   "cannot remove work-product cleanup")
            definition = definition.replace(addition, "")
    op.execute(sa.text(definition))


def upgrade() -> None:
    op.execute(_TABLE_SQL)
    op.execute(_INSERT_GUARD_SQL)
    _replace_retention(expanded=True)


def downgrade() -> None:
    # Fence writers before checking whether rollback would erase retained history.
    op.execute("LOCK TABLE public.case_work_product_versions IN ACCESS EXCLUSIVE MODE")
    # FORCE RLS can hide retained rows even from a non-BYPASSRLS table owner.
    # Alembic's PostgreSQL transaction restores FORCE on refusal; on success the
    # empty table is dropped. Runtime roles cannot perform this owner-only DDL.
    op.execute("ALTER TABLE public.case_work_product_versions NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.case_work_product_versions) THEN
                RAISE EXCEPTION 'cannot downgrade: internal work-product history is retained';
            END IF;
        END $$;
    """)
    _replace_retention(expanded=False)
    op.drop_table("case_work_product_versions")
    op.execute("DROP FUNCTION public.guard_case_work_product_insert()")
