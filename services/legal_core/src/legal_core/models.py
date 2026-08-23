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


class SubscriptionEntitlement(Base):
    __tablename__ = "subscription_entitlements"
    __table_args__ = (
        UniqueConstraint("clinic_id", "id"),
        UniqueConstraint("clinic_id", "user_id"),
        ForeignKeyConstraint(
            ["clinic_id", "user_id"],
            ["clinic_users.clinic_id", "clinic_users.user_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'CANCELLED')"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at"),
        Index("ix_subscription_entitlements_access", "user_id", "status", "starts_at"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    user_id: Mapped[UUID] = mapped_column(UUID_PK)
    status: Mapped[str] = mapped_column(String(20), server_default="ACTIVE")
    plan_code: Mapped[str] = mapped_column(String(80))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class SubscriptionEntitlementEvent(Base):
    __tablename__ = "subscription_entitlement_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["clinic_id", "entitlement_id"],
            ["subscription_entitlements.clinic_id", "subscription_entitlements.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("event_type IN ('GRANTED', 'UPDATED', 'SUSPENDED', 'CANCELLED')"),
        Index("ix_subscription_entitlement_events_tenant_time", "clinic_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    entitlement_id: Mapped[UUID] = mapped_column(UUID_PK)
    event_type: Mapped[str] = mapped_column(String(20))
    performed_by_user_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="RESTRICT")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


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
        UniqueConstraint("clinic_id", "case_id", "id"),
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


class RiskPolicyVersion(Base):
    """Platform-owned immutable policy content; status changes are reviewed separately."""

    __tablename__ = "risk_policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_key", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("status IN ('DRAFT', 'APPROVED', 'RETIRED')"),
        CheckConstraint("char_length(content_sha256) = 64"),
        Index(
            "uq_risk_policy_versions_one_approved",
            "policy_key",
            unique=True,
            postgresql_where=text("status = 'APPROVED'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    policy_key: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), server_default="DRAFT")
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class RiskPolicyEvent(Base):
    """Append-only legal-editor decision trail for a platform risk-policy version."""

    __tablename__ = "risk_policy_events"
    __table_args__ = (
        CheckConstraint("decision IN ('APPROVED', 'RETIRED', 'BLOCKED')"),
        CheckConstraint("char_length(expected_content_sha256) = 64"),
        Index("ix_risk_policy_events_policy_time", "risk_policy_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    risk_policy_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey("risk_policy_versions.id", ondelete="RESTRICT")
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="RESTRICT")
    )
    decision: Mapped[str] = mapped_column(String(20))
    expected_content_sha256: Mapped[str] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class CaseRiskAssessment(Base):
    """Append-only result of evaluating one policy over a frozen tenant case snapshot."""

    __tablename__ = "case_risk_assessments"
    __table_args__ = (
        UniqueConstraint("clinic_id", "id"),
        ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="RESTRICT"
        ),
        CheckConstraint("level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', 'UNAVAILABLE')"),
        CheckConstraint("char_length(fact_snapshot_sha256) = 64"),
        CheckConstraint("char_length(evidence_trace_sha256) = 64"),
        CheckConstraint("external_draft_allowed = false"),
        Index("ix_case_risk_assessments_tenant_case", "clinic_id", "case_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    case_id: Mapped[UUID] = mapped_column(UUID_PK)
    policy_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey("risk_policy_versions.id", ondelete="RESTRICT")
    )
    level: Mapped[str] = mapped_column(String(20))
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB)
    fact_snapshot_sha256: Mapped[str] = mapped_column(String(64))
    evidence_trace_sha256: Mapped[str] = mapped_column(String(64))
    external_draft_allowed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class CaseEscalation(Base):
    """Append-only required review generated from a HIGH or CRITICAL assessment."""

    __tablename__ = "case_escalations"
    __table_args__ = (
        UniqueConstraint("clinic_id", "case_risk_assessment_id"),
        ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["clinic_id", "case_risk_assessment_id"],
            ["case_risk_assessments.clinic_id", "case_risk_assessments.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("level IN ('HIGH', 'CRITICAL')"),
        CheckConstraint("status = 'REQUIRED'"),
        Index("ix_case_escalations_tenant_case", "clinic_id", "case_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    case_id: Mapped[UUID] = mapped_column(UUID_PK)
    case_risk_assessment_id: Mapped[UUID] = mapped_column(UUID_PK)
    level: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="REQUIRED")
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class CaseAnalysisRun(Base):
    """Immutable evidence/verifier outcome for one frozen case analysis attempt."""

    __tablename__ = "case_analysis_runs"
    __table_args__ = (
        UniqueConstraint("clinic_id", "id"),
        ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["clinic_id", "created_by_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["clinic_id", "case_risk_assessment_id"],
            ["case_risk_assessments.clinic_id", "case_risk_assessments.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("verifier_status IN ('PASSED', 'BLOCKED')"),
        CheckConstraint("char_length(fact_snapshot_sha256) = 64"),
        CheckConstraint("char_length(evidence_trace_sha256) = 64"),
        Index("ix_case_analysis_runs_tenant_case", "clinic_id", "case_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    case_id: Mapped[UUID] = mapped_column(UUID_PK)
    case_risk_assessment_id: Mapped[UUID | None] = mapped_column(UUID_PK)
    created_by_membership_id: Mapped[UUID] = mapped_column(UUID_PK)
    as_of_date: Mapped[date] = mapped_column(Date)
    fact_snapshot_sha256: Mapped[str] = mapped_column(String(64))
    evidence_trace_sha256: Mapped[str] = mapped_column(String(64))
    verifier_status: Mapped[str] = mapped_column(String(20))
    block_reason_codes_json: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class CaseAnalysisClaim(Base):
    """Hash-only claim evidence trail; raw generated text stays out of audit tables."""

    __tablename__ = "case_analysis_claims"
    __table_args__ = (
        UniqueConstraint("clinic_id", "analysis_run_id", "claim_id"),
        ForeignKeyConstraint(
            ["clinic_id", "analysis_run_id"],
            ["case_analysis_runs.clinic_id", "case_analysis_runs.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("claim_kind IN ('LEGAL', 'ACTION')"),
        CheckConstraint(
            "verification_result IN "
            "('VERIFIED', 'UNSUPPORTED', 'CONTRADICTED', 'NOT_APPLICABLE', 'INSUFFICIENT_FACTS')"
        ),
        CheckConstraint("char_length(claim_sha256) = 64"),
        Index("ix_case_analysis_claims_tenant_run", "clinic_id", "analysis_run_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    analysis_run_id: Mapped[UUID] = mapped_column(UUID_PK)
    claim_id: Mapped[str] = mapped_column(String(80))
    claim_kind: Mapped[str] = mapped_column(String(20))
    claim_sha256: Mapped[str] = mapped_column(String(64))
    verification_result: Mapped[str] = mapped_column(String(30))
    reason_code: Mapped[str | None] = mapped_column(String(80))
    evidence_fragment_ids_json: Mapped[list[str]] = mapped_column(JSONB)
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


class TelegramCaseWorkflow(Base):
    """Durable idempotency boundary for one Telegram intake submission."""

    __tablename__ = "telegram_case_workflows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["clinic_id", "actor_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["clinic_id", "case_id"],
            ["cases.clinic_id", "cases.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("state = 'SUCCEEDED'"),
        CheckConstraint("char_length(request_sha256) = 64"),
        ForeignKeyConstraint(
            ["clinic_id", "case_id", "report_id"],
            ["case_reports.clinic_id", "case_reports.case_id", "case_reports.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("case_id"),
        UniqueConstraint("report_id"),
        Index("ix_telegram_case_workflows_tenant", "clinic_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    clinic_id: Mapped[UUID] = mapped_column(UUID_PK)
    actor_membership_id: Mapped[UUID] = mapped_column(UUID_PK)
    request_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    case_id: Mapped[UUID] = mapped_column(UUID_PK)
    report_id: Mapped[UUID] = mapped_column(UUID_PK)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class LegalSource(Base):
    __tablename__ = "legal_sources"
    __table_args__ = (
        UniqueConstraint("source_key", "revision"),
        CheckConstraint(
            "(status = 'DRAFT' AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'APPROVED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_legal_sources_lifecycle",
        ),
    )

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
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from"),
        CheckConstraint(
            "artifact_kind IN ('NORMALIZED_EXCERPT', 'OFFICIAL_RAW')",
            name="ck_legal_versions_artifact_kind",
        ),
        CheckConstraint(
            "approval_state <> 'APPROVED' OR artifact_kind = 'OFFICIAL_RAW'",
            name="ck_legal_versions_approved_official_raw",
        ),
        CheckConstraint(
            "encode(digest(raw_bytes, 'sha256'), 'hex') = raw_sha256",
            name="ck_legal_versions_raw_sha256",
        ),
        CheckConstraint(
            "octet_length(raw_bytes) = raw_size_bytes",
            name="ck_legal_versions_raw_size",
        ),
        CheckConstraint(
            "encode(digest(convert_to(normalized_text, 'UTF8'), 'sha256'), 'hex') "
            "= normalized_sha256",
            name="ck_legal_versions_normalized_sha256",
        ),
        CheckConstraint(
            "normalization_scope IN ('SELECTED_EXCERPT', 'FULL_DOCUMENT')",
            name="ck_legal_versions_normalization_scope",
        ),
        CheckConstraint(
            "artifact_kind <> 'OFFICIAL_RAW' OR "
            "(artifact_retrieved_at IS NOT NULL AND normalization_scope = 'FULL_DOCUMENT' "
            "AND (raw_mime_type <> 'application/pdf' OR artifact_page_count IS NOT NULL))",
            name="ck_legal_versions_official_metadata",
        ),
        Index("ix_legal_versions_resolution", "document_id", "approval_state", "effective_from"),
        Index(
            "uq_legal_versions_approved_raw",
            "document_id",
            "raw_sha256",
            unique=True,
            postgresql_where=text("approval_state = 'APPROVED'"),
        ),
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
    artifact_kind: Mapped[str] = mapped_column(
        String(30), server_default="NORMALIZED_EXCERPT"
    )
    raw_sha256: Mapped[str] = mapped_column(String(64))
    raw_mime_type: Mapped[str] = mapped_column(String(100))
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    raw_size_bytes: Mapped[int] = mapped_column(BigInteger)
    artifact_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    artifact_page_count: Mapped[int | None] = mapped_column(Integer)
    normalized_text: Mapped[str] = mapped_column(Text)
    normalized_sha256: Mapped[str] = mapped_column(String(64))
    fragments_sha256: Mapped[str] = mapped_column(String(64))
    normalization_scope: Mapped[str] = mapped_column(String(30))
    parser_version: Mapped[str] = mapped_column(String(80))
    regression_passed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(UUID_PK, ForeignKey("users.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


class LegalApprovalEvent(Base):
    __tablename__ = "legal_approval_events"
    __table_args__ = (
        CheckConstraint("decision IN ('APPROVED', 'BLOCKED', 'REJECTED')"),
        CheckConstraint("char_length(expected_sha256) = 64"),
        CheckConstraint("char_length(regression_result_sha256) = 64"),
        CheckConstraint("char_length(policy_version) BETWEEN 1 AND 80"),
        Index("ix_legal_approval_events_version_time", "legal_version_id", "created_at", "id"),
        Index(
            "uq_legal_approval_events_approved",
            "legal_version_id",
            unique=True,
            postgresql_where=text("decision = 'APPROVED'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=text("gen_random_uuid()")
    )
    legal_version_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey("legal_versions.id", ondelete="RESTRICT")
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="RESTRICT")
    )
    decision: Mapped[str] = mapped_column(String(20))
    expected_sha256: Mapped[str] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(80))
    policy_version: Mapped[str] = mapped_column(String(80))
    regression_result_sha256: Mapped[str] = mapped_column(String(64))
    regression_checks_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    checks_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=UTC_NOW)


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
