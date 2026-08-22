"""SQLAlchemy persistence model for tenant cases and versioned law."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    pass


UUID_PK = Uuid(as_uuid=True)
UTC_NOW = text("timezone('utc', now())")


class Clinic(Base):
    __tablename__ = "clinics"
    __table_args__ = (CheckConstraint("char_length(name) BETWEEN 1 AND 200"),)

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("telegram_user_id > 0"),)

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), server_default="ACTIVE")
    system_role: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class ClinicUser(Base):
    __tablename__ = "clinic_users"
    __table_args__ = (
        UniqueConstraint("clinic_id", "user_id"),
        UniqueConstraint("clinic_id", "id"),
        Index("ix_clinic_users_resolution", "user_id", "status", "clinic_id"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("clinics.id", ondelete="RESTRICT"))
    user_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("users.id", ondelete="RESTRICT"))
    role: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), server_default="ACTIVE")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("clinic_id", "id"),
        ForeignKeyConstraint(
            ["clinic_id", "created_by_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("row_version > 0"),
        Index("ix_cases_tenant_updated", "clinic_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    case_no: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    created_by_membership_id: Mapped[UUID] = mapped_column(UUID_PK)
    status: Mapped[str] = mapped_column(String(30), server_default="COLLECTING")
    intake_schema_version: Mapped[str] = mapped_column(
        String(80), server_default="dental-case-intake.v1"
    )
    primary_incident_type: Mapped[str | None] = mapped_column(String(50))
    incident_tags: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    title: Mapped[str | None] = mapped_column(String(240))
    service_date: Mapped[date | None] = mapped_column(Date)
    incident_date: Mapped[date | None] = mapped_column(Date)
    claim_date: Mapped[date | None] = mapped_column(Date)
    row_version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseFact(Base):
    __tablename__ = "case_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["clinic_id", "recorded_by_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("clinic_id", "case_id", "fact_key", "revision"),
        CheckConstraint("revision > 0"),
        CheckConstraint("NOT (source_type = 'INFERENCE' AND evidence_status = 'CONFIRMED')"),
        Index("ix_case_facts_current", "clinic_id", "case_id", "fact_key", "revision"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    case_id: Mapped[UUID] = mapped_column(UUID_PK)
    fact_key: Mapped[str] = mapped_column(String(60))
    revision: Mapped[int] = mapped_column(Integer)
    value_type: Mapped[str] = mapped_column(String(30))
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_type: Mapped[str] = mapped_column(String(30))
    source_ref_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence_status: Mapped[str] = mapped_column(String(20), server_default="UNVERIFIED")
    recorded_by_membership_id: Mapped[UUID] = mapped_column(UUID_PK)
    supersedes_fact_id: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("case_facts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class CaseReport(Base):
    __tablename__ = "case_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["clinic_id", "created_by_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("clinic_id", "case_id", "report_version"),
        CheckConstraint("report_version > 0"),
        CheckConstraint("pdf_size_bytes > 0"),
        Index("ix_case_reports_latest", "clinic_id", "case_id", "report_version"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    case_id: Mapped[UUID] = mapped_column(UUID_PK)
    report_version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))
    report_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    pdf_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    pdf_sha256: Mapped[str] = mapped_column(String(64))
    pdf_size_bytes: Mapped[int] = mapped_column(BigInteger)
    facts_snapshot_sha256: Mapped[str] = mapped_column(String(64))
    created_by_membership_id: Mapped[UUID] = mapped_column(UUID_PK)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["clinic_id", "actor_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_audit_events_tenant_time", "clinic_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    actor_membership_id: Mapped[UUID] = mapped_column(UUID_PK)
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[UUID] = mapped_column(UUID_PK)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    correlation_id: Mapped[UUID] = mapped_column(UUID_PK)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["clinic_id", "actor_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("scope", "actor_membership_id", "key"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    actor_membership_id: Mapped[UUID] = mapped_column(UUID_PK)
    scope: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(128))
    request_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), server_default="IN_PROGRESS")
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[UUID | None] = mapped_column(UUID_PK)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class LegalSource(Base):
    __tablename__ = "legal_sources"
    __table_args__ = (UniqueConstraint("source_key", "revision"),)

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_key: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(Text)
    allowed_hosts: Mapped[list[str]] = mapped_column(JSONB)
    trust_level: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="DRAFT")
    approved_by: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class LegalDocument(Base):
    __tablename__ = "legal_documents"

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    canonical_key: Mapped[str] = mapped_column(String(120), unique=True)
    jurisdiction: Mapped[str] = mapped_column(String(8), server_default="RU")
    document_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str] = mapped_column(String(240))
    official_number: Mapped[str | None] = mapped_column(String(80))
    adoption_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class LegalVersion(Base):
    __tablename__ = "legal_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no"),
        UniqueConstraint("document_id", "raw_sha256"),
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from"),
        Index("ix_legal_versions_resolution", "document_id", "approval_state", "effective_from"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    document_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("legal_documents.id"))
    source_id: Mapped[UUID] = mapped_column(UUID_PK, ForeignKey("legal_sources.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    source_external_id: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str] = mapped_column(Text)
    publication_date: Mapped[date | None] = mapped_column(Date)
    version_date: Mapped[date | None] = mapped_column(Date)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    approval_state: Mapped[str] = mapped_column(String(30), server_default="REVIEW_REQUIRED")
    raw_sha256: Mapped[str] = mapped_column(String(64))
    raw_mime_type: Mapped[str] = mapped_column(String(100))
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    normalized_text: Mapped[str] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(80))
    regression_passed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("users.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class LegalFragment(Base):
    __tablename__ = "legal_fragments"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal"),
        Index("ix_legal_fragments_structure", "version_id", "article", "part", "point"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    version_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey("legal_versions.id", ondelete="RESTRICT")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    article: Mapped[str | None] = mapped_column(String(80))
    part: Mapped[str | None] = mapped_column(String(80))
    point: Mapped[str | None] = mapped_column(String(80))
    heading: Mapped[str | None] = mapped_column(Text)
    structural_path: Mapped[str] = mapped_column(Text)
    fragment_text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)
