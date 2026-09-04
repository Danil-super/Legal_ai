"""Validated REST contracts for Case Core."""

import json
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from legal_core.contracts import CaseStatus, ContractModel, FactKey, MissingFact

_TEXT_FACT_KEYS = frozenset(
    {FactKey.SERVICE_TYPE, FactKey.PROBLEM_SUMMARY, FactKey.AUTHORITY_KIND}
)
_DATE_FACT_KEYS = frozenset(
    {
        FactKey.SERVICE_DATE,
        FactKey.INCIDENT_DATE,
        FactKey.CLAIM_DATE,
        FactKey.CLAIM_RECEIVED_AT,
        FactKey.RESPONSE_DEADLINE,
        FactKey.DOCUMENT_DATE,
    }
)
_BOOLEAN_FACT_KEYS = frozenset(
    {
        FactKey.FORMAL_CLAIM,
        FactKey.HARM_CLAIMED,
        FactKey.HOSPITALIZATION,
        FactKey.LAWYER_CONTACT,
        FactKey.REPRESENTATIVE_AUTHORITY,
        FactKey.REGULATOR_OR_COURT,
        FactKey.REGULATOR_THREAT,
    }
)
_ENUM_SET_FACT_KEYS = frozenset({FactKey.INCIDENT_TYPES, FactKey.PATIENT_DEMAND})
_ENUM_FACT_KEYS = frozenset({FactKey.PRIMARY_INCIDENT_TYPE})
_DOCUMENT_STATUSES = frozenset(
    {"AVAILABLE", "MISSING", "UNKNOWN", "REQUESTED", "NOT_APPLICABLE"}
)
_DOCUMENT_KEYS = frozenset(
    {"CONTRACT", "MEDICAL_RECORD", "INFORMED_CONSENT", "GUARANTEE"}
)
_SIGNAL_STATES = frozenset({"YES", "NO", "UNKNOWN"})
_DRAFT_DATA_KEYS = frozenset(
    {
        "incident_type",
        "service_type",
        "service_date",
        "incident_date",
        "claim_date",
        "problem_summary",
        "patient_demand",
        "demand_amount_kopecks",
        "formal_claim",
        "claim_received_at",
        "response_deadline",
        "harm_claimed",
        "hospitalization",
        "lawyer_contact",
        "representative_authority",
        "regulator_or_court",
        "authority_kind",
        "authority_document_date",
        "regulator_threat",
        "documents_status",
    }
)
TelegramDraftWizardState = Literal[
    "INCIDENT",
    "SERVICE_TYPE",
    "SERVICE_DATE",
    "INCIDENT_DATE",
    "CLAIM_DATE",
    "PROBLEM_SUMMARY",
    "PATIENT_DEMAND",
    "DEMAND_AMOUNT",
    "FORMAL_CLAIM",
    "CLAIM_RECEIVED_AT",
    "CLAIM_DEADLINE",
    "HARM",
    "HOSPITALIZATION",
    "LAWYER",
    "REPRESENTATIVE_AUTHORITY",
    "LAWYER_DEADLINE",
    "AUTHORITY",
    "AUTHORITY_KIND",
    "AUTHORITY_DATE",
    "AUTHORITY_DEADLINE",
    "REGULATOR_THREAT",
    "DOCUMENTS",
    "CONFIRM",
]


def _exact_keys(value: dict[str, Any], keys: set[str], fact_key: FactKey) -> None:
    if set(value) != keys:
        raise ValueError(f"{fact_key.value} has an invalid value shape")


def _nonempty_token(value: object, fact_key: FactKey) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 80
        or any(
            not (character.isupper() or character.isdigit() or character == "_")
            for character in value
        )
    ):
        raise ValueError(f"{fact_key.value} requires an uppercase enum token")


def _unique_fact_keys(facts: list["FactInput"]) -> list["FactInput"]:
    keys = [fact.fact_key for fact in facts]
    if len(keys) != len(set(keys)):
        raise ValueError("fact keys must be unique")
    return facts


class CreateCaseRequest(ContractModel):
    intake_schema_version: Literal["dental-case-intake.v1"] = Field(alias="intakeSchemaVersion")
    channel: Literal["TELEGRAM"]


class CaseResponse(ContractModel):
    id: UUID
    public_number: str = Field(alias="publicNumber")
    status: CaseStatus
    intake_schema_version: str = Field(alias="intakeSchemaVersion")
    created_at: datetime = Field(alias="createdAt")


ClinicRole = Literal["CLINIC_OWNER", "CLINIC_ADMIN", "CLINIC_LAWYER"]


class ActorResponse(ContractModel):
    role: ClinicRole


class ClinicMemberResponse(ContractModel):
    telegram_user_id: int = Field(alias="telegramUserId")
    role: ClinicRole


class ClinicMemberListResponse(ContractModel):
    items: list[ClinicMemberResponse]


class ClinicMemberCreateRequest(ContractModel):
    telegram_user_id: int = Field(alias="telegramUserId", gt=0, le=9_223_372_036_854_775_807)
    role: Literal["CLINIC_ADMIN", "CLINIC_LAWYER"]


class EscalationDiscussionMessageRequest(ContractModel):
    body: str = Field(min_length=1, max_length=1500)

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("body must not be blank")
        return normalized


class EscalationDiscussionMessageResponse(ContractModel):
    id: UUID
    author_role: ClinicRole = Field(alias="authorRole")
    body: str
    created_at: datetime = Field(alias="createdAt")


class EscalationDiscussionResponse(ContractModel):
    items: list[EscalationDiscussionMessageResponse]


class EscalationQueueItemResponse(ContractModel):
    escalation_id: UUID = Field(alias="escalationId")
    public_number: str = Field(alias="publicNumber", min_length=1, max_length=40)
    risk_level: Literal["HIGH", "CRITICAL"] = Field(alias="riskLevel")
    reason_codes: list[str] = Field(alias="reasonCodes", min_length=1, max_length=20)
    created_at: datetime = Field(alias="createdAt")


class EscalationQueueResponse(ContractModel):
    items: list[EscalationQueueItemResponse] = Field(default_factory=list, max_length=100)


class TelegramIntakeDraftUpdateRequest(ContractModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    wizard_state: TelegramDraftWizardState = Field(alias="wizardState")
    draft_data: dict[str, Any] = Field(alias="draftData")

    @field_validator("draft_data")
    @classmethod
    def validate_draft_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not set(value) <= _DRAFT_DATA_KEYS:
            raise ValueError("draftData contains unsupported fields")
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > 16_384:
            raise ValueError("draftData exceeds 16384 bytes")
        return value


class TelegramIntakeDraftCreateRequest(ContractModel):
    pass


class TelegramIntakeDraftArchiveRequest(ContractModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class TelegramIntakeDraftSummary(ContractModel):
    id: UUID
    wizard_state: TelegramDraftWizardState = Field(alias="wizardState")
    revision: int
    incident_type: str | None = Field(alias="incidentType")
    updated_at: datetime = Field(alias="updatedAt")


class TelegramIntakeDraftListResponse(ContractModel):
    items: list[TelegramIntakeDraftSummary]


class TelegramIntakeDraftResponse(TelegramIntakeDraftSummary):
    draft_data: dict[str, Any] = Field(alias="draftData")
    purge_after: datetime = Field(alias="purgeAfter")


class PlatformSubscriptionGrantRequest(ContractModel):
    telegram_user_id: int = Field(alias="telegramUserId", gt=0, le=9_223_372_036_854_775_807)
    plan_code: Literal["MVP_MANUAL", "FREE_PILOT"] = Field(
        default="MVP_MANUAL", alias="planCode"
    )
    pilot_days: int | None = Field(default=None, alias="pilotDays", ge=1, le=90)

    @model_validator(mode="after")
    def validate_pilot_duration(self) -> "PlatformSubscriptionGrantRequest":
        if self.plan_code == "FREE_PILOT" and self.pilot_days is None:
            raise ValueError("pilotDays is required for FREE_PILOT")
        if self.plan_code == "MVP_MANUAL" and self.pilot_days is not None:
            raise ValueError("pilotDays is allowed only for FREE_PILOT")
        return self


class PlatformSubscriptionGrantResponse(ContractModel):
    telegram_user_id: int = Field(alias="telegramUserId")
    clinic_name: str = Field(alias="clinicName")
    plan_code: Literal["MVP_MANUAL", "FREE_PILOT"] = Field(alias="planCode")
    status: Literal["ACTIVE"]
    ends_at: datetime | None = Field(alias="endsAt")


class LegalFragmentResponse(ContractModel):
    fragment_id: UUID = Field(alias="fragmentId")
    version_id: UUID = Field(alias="versionId")
    document_id: UUID = Field(alias="documentId")
    article: str | None
    part: str | None
    point: str | None
    structural_path: str = Field(alias="structuralPath")
    fragment_text: str = Field(alias="fragmentText")
    text_sha256: str = Field(alias="textSha256")
    effective_from: date = Field(alias="effectiveFrom")
    effective_to: date | None = Field(alias="effectiveTo")
    source_url: str = Field(alias="sourceUrl")
    raw_sha256: str = Field(alias="rawSha256")
    document_title: str = Field(alias="documentTitle")
    issuer: str
    official_number: str | None = Field(alias="officialNumber")
    version_date: date | None = Field(alias="versionDate")
    publication_date: date | None = Field(alias="publicationDate")


class LegalFragmentSearchResponse(ContractModel):
    items: list[LegalFragmentResponse]


class LegalLibraryDocumentResponse(ContractModel):
    """One approved legal-document version currently eligible for report retrieval."""

    document_id: UUID = Field(alias="documentId")
    version_id: UUID = Field(alias="versionId")
    document_title: str = Field(alias="documentTitle", min_length=1, max_length=1_000)
    issuer: str = Field(min_length=1, max_length=240)
    official_number: str | None = Field(default=None, alias="officialNumber", max_length=80)
    effective_from: date = Field(alias="effectiveFrom")
    effective_to: date | None = Field(default=None, alias="effectiveTo")
    source_url: str = Field(alias="sourceUrl", min_length=1, max_length=2_000)
    raw_sha256: str = Field(alias="rawSha256", pattern=r"^[0-9a-f]{64}$")
    fragment_count: int = Field(alias="fragmentCount", ge=1, le=10_000)


class LegalLibraryResponse(ContractModel):
    """Read-only legal-base index for the lawyer and clinic owner workspace."""

    as_of_date: date = Field(alias="asOfDate")
    items: list[LegalLibraryDocumentResponse] = Field(default_factory=list, max_length=50)


class FactInput(ContractModel):
    fact_key: FactKey = Field(alias="factKey")
    value_type: Literal[
        "TEXT",
        "BOOLEAN",
        "DATE",
        "MONEY",
        "ENUM",
        "ENUM_SET",
        "DOCUMENT_INVENTORY",
    ] = Field(alias="valueType")
    value: dict[str, Any]
    source_type: Literal["USER_STATEMENT"] = Field(alias="sourceType")

    @field_validator("value")
    @classmethod
    def validate_value_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > 4_096:
            raise ValueError("fact value exceeds 4096 bytes")
        return value

    @model_validator(mode="after")
    def validate_fact_semantics(self) -> "FactInput":
        expected_type: str
        if self.fact_key in _TEXT_FACT_KEYS:
            expected_type = "TEXT"
            _exact_keys(self.value, {"text"}, self.fact_key)
            text = self.value["text"]
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1_500:
                raise ValueError(
                    f"{self.fact_key.value} requires non-empty text up to 1500 characters"
                )
        elif self.fact_key in _DATE_FACT_KEYS:
            expected_type = "DATE"
            _exact_keys(self.value, {"date", "precision"}, self.fact_key)
            date_value = self.value["date"]
            precision = self.value["precision"]
            if precision == "UNKNOWN":
                if date_value is not None:
                    raise ValueError(
                        f"{self.fact_key.value} cannot include a date with UNKNOWN precision"
                    )
            elif precision in {"EXACT", "APPROXIMATE"}:
                if not isinstance(date_value, str):
                    raise ValueError(f"{self.fact_key.value} requires an ISO date")
                try:
                    date.fromisoformat(date_value)
                except ValueError as exc:
                    raise ValueError(
                        f"{self.fact_key.value} requires a valid ISO date"
                    ) from exc
            else:
                raise ValueError(f"{self.fact_key.value} has an invalid date precision")
        elif self.fact_key in _BOOLEAN_FACT_KEYS:
            expected_type = "BOOLEAN"
            if set(self.value) == {"boolean"}:
                if not isinstance(self.value["boolean"], bool):
                    raise ValueError(f"{self.fact_key.value} requires a boolean value")
            elif set(self.value) == {"state"}:
                if self.value["state"] not in _SIGNAL_STATES:
                    raise ValueError(f"{self.fact_key.value} has an invalid signal state")
            else:
                raise ValueError(f"{self.fact_key.value} has an invalid value shape")
        elif self.fact_key in _ENUM_SET_FACT_KEYS:
            expected_type = "ENUM_SET"
            _exact_keys(self.value, {"values"}, self.fact_key)
            values = self.value["values"]
            if not isinstance(values, list) or not 1 <= len(values) <= 10:
                raise ValueError(f"{self.fact_key.value} requires one to ten enum tokens")
            for value in values:
                _nonempty_token(value, self.fact_key)
            if len(values) != len(set(values)):
                raise ValueError(f"{self.fact_key.value} enum tokens must be unique")
        elif self.fact_key in _ENUM_FACT_KEYS:
            expected_type = "ENUM"
            _exact_keys(self.value, {"value"}, self.fact_key)
            _nonempty_token(self.value["value"], self.fact_key)
        elif self.fact_key == FactKey.DEMAND_AMOUNT:
            expected_type = "MONEY"
            _exact_keys(self.value, {"amountKopecks", "currency"}, self.fact_key)
            amount = self.value["amountKopecks"]
            if (
                isinstance(amount, bool)
                or not isinstance(amount, int)
                or not 1 <= amount <= 100_000_000_000
            ):
                raise ValueError("DEMAND_AMOUNT requires a positive integer number of kopecks")
            if self.value["currency"] != "RUB":
                raise ValueError("DEMAND_AMOUNT currency must be RUB")
        elif self.fact_key == FactKey.CLINIC_DOCUMENTS:
            expected_type = "DOCUMENT_INVENTORY"
            if not 1 <= len(self.value) <= len(_DOCUMENT_KEYS):
                raise ValueError("CLINIC_DOCUMENTS requires a non-empty document inventory")
            if (
                not set(self.value) <= _DOCUMENT_KEYS
                or not set(self.value.values()) <= _DOCUMENT_STATUSES
            ):
                raise ValueError("CLINIC_DOCUMENTS contains an unsupported document key or status")
        else:  # pragma: no cover - FactKey exhaustiveness is protected by the tests above.
            raise ValueError(f"Unsupported fact key: {self.fact_key.value}")

        if self.value_type != expected_type:
            raise ValueError(f"{self.fact_key.value} requires valueType {expected_type}")
        return self


class AddFactsRequest(ContractModel):
    question_id: str = Field(alias="questionId", min_length=1, max_length=80)
    intake_schema_version: Literal["dental-case-intake.v1"] = Field(alias="intakeSchemaVersion")
    facts: list[FactInput] = Field(min_length=1, max_length=20)

    @field_validator("facts")
    @classmethod
    def fact_keys_are_unique(cls, facts: list[FactInput]) -> list[FactInput]:
        return _unique_fact_keys(facts)


class IntakeResponse(ContractModel):
    case_id: UUID = Field(alias="caseId")
    status: CaseStatus
    missing_facts: list[MissingFact] = Field(alias="missingFacts")
    next_question_id: str | None = Field(alias="nextQuestionId")


class FinalizeRequest(ContractModel):
    pass


class CreateReportRequest(ContractModel):
    locale: Literal["ru-RU"] = "ru-RU"


class ReportResponse(ContractModel):
    id: UUID
    case_id: UUID = Field(alias="caseId")
    report_version: int = Field(alias="reportVersion")
    report_json: dict[str, Any] = Field(alias="reportJson")
    pdf_sha256: str = Field(alias="pdfSha256")
    created_at: datetime = Field(alias="createdAt")


class TelegramWorkflowSubmissionRequest(ContractModel):
    intake_schema_version: Literal["dental-case-intake.v1"] = Field(
        alias="intakeSchemaVersion"
    )
    locale: Literal["ru-RU"] = "ru-RU"
    facts: list[FactInput] = Field(min_length=1, max_length=20)

    @field_validator("facts")
    @classmethod
    def fact_keys_are_unique(cls, facts: list[FactInput]) -> list[FactInput]:
        return _unique_fact_keys(facts)


class TelegramWorkflowResponse(ContractModel):
    workflow_id: UUID = Field(alias="workflowId")
    state: Literal["SUCCEEDED"]
    case: CaseResponse
    report: ReportResponse
