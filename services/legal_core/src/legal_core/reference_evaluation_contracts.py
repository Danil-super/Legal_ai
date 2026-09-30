"""Public metadata contracts for the de-identified reference-evaluation workspace."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from legal_core.contracts import ContractModel

ReferenceEvaluationGroup = Literal[
    "clinical", "labour", "courts", "privacy", "licensing", "healthcare", "general"
]
ReferenceExpectedRoute = Literal["ABSTAIN", "HUMAN_ESCALATION", "INTERNAL_DRAFT"]
ReferenceEvaluationStatus = Literal[
    "DRAFT",
    "READY_FOR_REVIEW",
    "CHANGES_REQUIRED",
    "APPROVED_FOR_EVALUATION",
    "REJECTED",
    "RETIRED",
]
ReferenceReviewDecision = Literal[
    "APPROVE_FOR_EVALUATION", "CHANGES_REQUIRED", "REJECT", "RETIRE"
]
ReferenceMaterialMimeType = Literal[
    "text/plain",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]


class ReferenceEvaluationAccessResponse(ContractModel):
    can_contribute: bool = Field(alias="canContribute")
    can_review: bool = Field(alias="canReview")


class ReferenceEvaluationCreateRequest(ContractModel):
    group_key: ReferenceEvaluationGroup = Field(alias="groupKey")
    as_of_date: date = Field(alias="asOfDate")
    expected_route: ReferenceExpectedRoute = Field(alias="expectedRoute")
    scenario_text: str = Field(alias="scenarioText", min_length=10, max_length=20_000)

    @model_validator(mode="after")
    def validate_text(self) -> "ReferenceEvaluationCreateRequest":
        if not self.scenario_text.strip():
            raise ValueError("scenarioText must not be blank")
        return self


class ReferenceEvaluationRevisionRequest(ContractModel):
    as_of_date: date = Field(alias="asOfDate")
    expected_route: ReferenceExpectedRoute = Field(alias="expectedRoute")
    scenario_text: str = Field(alias="scenarioText", min_length=10, max_length=20_000)

    @model_validator(mode="after")
    def validate_text(self) -> "ReferenceEvaluationRevisionRequest":
        if not self.scenario_text.strip():
            raise ValueError("scenarioText must not be blank")
        return self


class ReferenceEvaluationSummary(ContractModel):
    id: UUID
    display_name: str = Field(alias="displayName", min_length=1, max_length=120)
    group_key: ReferenceEvaluationGroup = Field(alias="groupKey")
    status: ReferenceEvaluationStatus
    current_version: int = Field(alias="currentVersion", ge=1)
    has_material: bool = Field(alias="hasMaterial")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ReferenceEvaluationListResponse(ContractModel):
    items: list[ReferenceEvaluationSummary] = Field(default_factory=list, max_length=50)
    next_before: UUID | None = Field(default=None, alias="nextBefore")


class ReferenceEvaluationDetail(ReferenceEvaluationSummary):
    as_of_date: date = Field(alias="asOfDate")
    expected_route: ReferenceExpectedRoute = Field(alias="expectedRoute")
    scenario_text: str | None = Field(alias="scenarioText", max_length=20_000)
    content_purged_at: datetime | None = Field(alias="contentPurgedAt")
    created_by_self: bool = Field(alias="createdBySelf")


class ReferenceEvaluationCreateResponse(ContractModel):
    case: ReferenceEvaluationDetail


class ReferenceEvaluationMaterialResponse(ContractModel):
    case: ReferenceEvaluationSummary
    mime_type: ReferenceMaterialMimeType = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes", ge=1, le=15_000_000)


class ReferenceEvaluationReviewRequest(ContractModel):
    decision: ReferenceReviewDecision
    note: str | None = Field(default=None, min_length=2, max_length=1_000)

    @model_validator(mode="after")
    def require_note_for_changes(self) -> "ReferenceEvaluationReviewRequest":
        if self.decision == "CHANGES_REQUIRED" and not (self.note and self.note.strip()):
            raise ValueError("note is required when changes are requested")
        return self


class ReferenceEvaluationReviewResponse(ContractModel):
    case: ReferenceEvaluationSummary
    decision: ReferenceReviewDecision
    reviewed_at: datetime = Field(alias="reviewedAt")
