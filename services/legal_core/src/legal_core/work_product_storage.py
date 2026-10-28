"""Append-only storage metadata, not authority to generate or approve a work product.

Future writers must reauthorize the actor, current case facts/evidence and worker
lease under the case lock. These constraints establish storage integrity only.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from legal_core.models import UTC_NOW, UUID_PK, Base

_PAYLOAD_BINDING = """(
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
) IS TRUE"""


class CaseWorkProductVersion(Base):
    __tablename__ = "case_work_product_versions"
    __table_args__ = (
        UniqueConstraint("clinic_id", "case_id", "work_product_id", "version",
                         name="uq_work_product_case_version"),
        ForeignKeyConstraint(["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["clinic_id", "author_membership_id"],
                             ["clinic_users.clinic_id", "clinic_users.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["clinic_id", "reviewed_by_membership_id"],
                             ["clinic_users.clinic_id", "clinic_users.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["clinic_id", "case_id", "work_product_id", "parent_version"],
                             ["case_work_product_versions.clinic_id",
                              "case_work_product_versions.case_id",
                              "case_work_product_versions.work_product_id",
                              "case_work_product_versions.version"], ondelete="RESTRICT",
                             name="fk_work_product_parent_version"),
        CheckConstraint("version > 0 AND ((version = 1 AND parent_version IS NULL) OR "
                        "(version > 1 AND parent_version IS NOT NULL "
                        "AND parent_version=version-1))",
                        name="ck_work_product_parent"),
        CheckConstraint("schema_version = 'ai-first-work-product.v1'",
                        name="ck_work_product_schema"),
        CheckConstraint("origin IN ('AI','HUMAN') AND "
                        "(version > 1 OR origin = 'AI') AND ("
                        "(origin='AI' AND review_state='UNAPPROVED' "
                        "AND reviewed_by_membership_id IS NULL AND reviewed_at IS NULL) OR "
                        "(origin='HUMAN' AND review_state='HUMAN_REVISED' "
                        "AND reviewed_by_membership_id IS NULL AND reviewed_at IS NULL) OR "
                        "(origin='HUMAN' AND review_state='HUMAN_APPROVED' "
                        "AND reviewed_by_membership_id=author_membership_id "
                        "AND reviewed_by_membership_id IS NOT NULL AND reviewed_at IS NOT NULL))",
                        name="ck_work_product_review_metadata"),
        CheckConstraint("jsonb_typeof(payload_json)='object' "
                        "AND octet_length(payload_json::text) <= 65536",
                        name="ck_work_product_payload_budget"),
        CheckConstraint(_PAYLOAD_BINDING, name="ck_work_product_payload_binding"),
        CheckConstraint("payload_json#>>'{source,context,referenceExampleTraceSha256}' "
                        "IS NOT DISTINCT FROM reference_example_trace_sha256",
                        name="ck_work_product_reference_trace"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$' AND content_sha256 = "
                        "encode(public.digest(convert_to("
                        "public.legal_canonical_jsonb(payload_json), "
                        "'UTF8'), 'sha256'), 'hex')", name="ck_work_product_content_hash"),
        CheckConstraint("fact_snapshot_sha256 ~ '^[0-9a-f]{64}$' AND "
                        "(evidence_trace_sha256 IS NULL OR "
                        "evidence_trace_sha256 ~ '^[0-9a-f]{64}$') AND "
                        "(clinic_document_context_trace_sha256 IS NULL OR "
                        "clinic_document_context_trace_sha256 ~ '^[0-9a-f]{64}$') AND "
                        "(reference_example_trace_sha256 IS NULL OR "
                        "reference_example_trace_sha256 ~ '^[0-9a-f]{64}$') AND "
                        "((evidence_trace_sha256 IS NULL) = "
                        "(clinic_document_context_trace_sha256 IS NULL))",
                        name="ck_work_product_snapshot_hashes"),
        CheckConstraint("char_length(btrim(risk_policy_version)) BETWEEN 1 AND 80",
                        name="ck_work_product_policy"),
        Index("ix_work_product_case_versions", "clinic_id", "case_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True,
                                   server_default=text("gen_random_uuid()"))
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    case_id: Mapped[UUID] = mapped_column(UUID_PK)
    work_product_id: Mapped[UUID] = mapped_column(UUID_PK)
    version: Mapped[int] = mapped_column(Integer)
    parent_version: Mapped[int | None] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(10))
    review_state: Mapped[str] = mapped_column(String(20))
    author_membership_id: Mapped[UUID] = mapped_column(UUID_PK)
    reviewed_by_membership_id: Mapped[UUID | None] = mapped_column(UUID_PK)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    source_operation_id: Mapped[UUID] = mapped_column(UUID_PK)
    fact_snapshot_sha256: Mapped[str] = mapped_column(String(64))
    evidence_trace_sha256: Mapped[str | None] = mapped_column(String(64))
    clinic_document_context_trace_sha256: Mapped[str | None] = mapped_column(String(64))
    reference_example_trace_sha256: Mapped[str | None] = mapped_column(String(64))
    risk_policy_version: Mapped[str] = mapped_column(String(80))
    as_of_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)
