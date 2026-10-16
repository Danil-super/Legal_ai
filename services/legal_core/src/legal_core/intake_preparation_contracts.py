"""Unconfirmed existing intake fields, grounded by offsets rather than raw-text copies."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, field_validator, model_validator

from legal_core.analysis_clarifications import AnalysisClarificationAnswers, ServiceProvidedDate
from legal_core.case_preparation_contracts import PreparationModel, Sha256
from legal_core.factual_safety_intake import validate_partial_screening
from legal_core.pseudonymization import contains_obvious_direct_identifier

ServiceName = Annotated[str, Field(strict=True, min_length=2, max_length=120)]


class UnderstandingContextRequest(PreparationModel):
    """Internal metadata only. The lease must never enter a public receipt."""

    lease_token: UUID = Field(alias="leaseToken")
    expected_revision: int = Field(alias="expectedRevision", strict=True, ge=1)
    expected_input_sha256: Sha256 = Field(alias="expectedInputSha256")


class StartUnderstandingRequest(PreparationModel):
    expected_revision: int = Field(alias="expectedRevision", strict=True, ge=1)


class UnderstandingInvocation(UnderstandingContextRequest):
    draft_id: UUID = Field(alias="draftId")


class IntakeUnderstandingContext(PreparationModel):
    """Private Core→Agent input, never a public receipt or a confirmation."""

    operation_id: UUID = Field(alias="operationId")
    draft_id: UUID = Field(alias="draftId")
    draft_revision: int = Field(alias="draftRevision", strict=True, ge=1)
    input_sha256: Sha256 = Field(alias="inputSha256")
    operation_kind: Literal["UNDERSTAND_INTAKE"] = Field(alias="operationKind")
    source: str = Field(strict=True, min_length=10, max_length=1500)


class SourceSpan(PreparationModel):
    start: int = Field(strict=True, ge=0, le=1500)
    end: int = Field(strict=True, ge=1, le=1500)

    @model_validator(mode="after")
    def positive_span(self) -> SourceSpan:
        if self.end <= self.start:
            raise ValueError("source span must not be empty or reversed")
        return self


class DraftSuggestions(PreparationModel):
    """Suggestions only; no role, legal qualification, report or confirmation flag."""

    incoming_kind: Literal[
        "MESSAGE_OR_REQUEST", "COMPLAINT", "FORMAL_DOCUMENT",
        "AUTHORITY_OR_COURT_DOCUMENT", "OTHER", "UNKNOWN",
    ] | None = Field(default=None, alias="incomingKind")
    situation_areas: list[Literal[
        "TREATMENT", "SERVICE", "MEDICAL_RECORDS", "PERSONAL_DATA", "OTHER",
    ]] | None = Field(default=None, alias="situationAreas", min_length=1, max_length=5)
    affected_services: list[ServiceName] | None = Field(
        default=None, alias="affectedServices", min_length=1, max_length=5,
    )
    event_date: ServiceProvidedDate | None = Field(default=None, alias="eventDate")
    conflict_stage: Literal["FIRST", "ONGOING", "UNKNOWN"] | None = Field(
        default=None, alias="conflictStage",
    )
    clinic_actions: list[Literal[
        "NOTHING_YET", "INVITED_FOR_EXAMINATION", "CONTINUING_TREATMENT",
        "HELD_MEDICAL_COMMISSION", "OFFERED_CORRECTION", "OFFERED_REFUND",
        "REMOVED_MATERIAL", "TERMINATED_CONTRACT", "SIGNED_AGREEMENT", "OTHER",
    ]] | None = Field(default=None, alias="clinicActions", min_length=1, max_length=10)
    health_signals: list[Literal[
        "NO_KNOWN_INFORMATION", "COMPLICATION_OR_WORSENING", "OTHER_CLINIC",
        "HOSPITALIZATION", "OTHER_CONSEQUENCE", "UNKNOWN",
    ]] | None = Field(default=None, alias="healthSignals", min_length=1, max_length=6)
    safety_screening: dict[str, JsonValue] | None = Field(default=None, alias="safetyScreening")
    analysis_clarification_answers: AnalysisClarificationAnswers | None = Field(
        default=None, alias="analysisClarificationAnswers",
    )
    source_spans: dict[str, list[SourceSpan]] = Field(alias="sourceSpans", max_length=9)

    @field_validator("safety_screening")
    @classmethod
    def closed_screening(cls, value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        if value is not None:
            validate_partial_screening(value)
        return value

    @model_validator(mode="after")
    def grounded_bounded_fields(self) -> DraftSuggestions:
        proposed = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        del proposed["sourceSpans"]
        if set(proposed) != set(self.source_spans):
            raise ValueError("each proposed field requires its own source spans")
        if any(not 1 <= len(spans) <= 3 for spans in self.source_spans.values()):
            raise ValueError("each proposed field requires one to three source spans")
        for value in (
            self.situation_areas, self.affected_services, self.clinic_actions, self.health_signals,
        ):
            if value is not None and len(set(value)) != len(value):
                raise ValueError("suggested choices must be unique")
        if (
            self.clinic_actions and "NOTHING_YET" in self.clinic_actions
            and len(self.clinic_actions) != 1
        ):
            raise ValueError("nothing yet cannot be combined with performed actions")
        if (
            self.health_signals and {"NO_KNOWN_INFORMATION", "UNKNOWN"} & set(self.health_signals)
            and len(self.health_signals) != 1
        ):
            raise ValueError("unknown/absent health information is exclusive")
        if any(contains_obvious_direct_identifier(value) for value in self.affected_services or []):
            raise ValueError("suggested service names must not contain direct identifiers")
        return self

    def validate_source(self, source: str) -> None:
        """Offsets prove a text anchor, not semantic truth; explicit confirmation still required."""
        if not 10 <= len(source) <= 1500 or contains_obvious_direct_identifier(source):
            raise ValueError("understanding source must be bounded and de-identified")
        if any(span.end > len(source) for spans in self.source_spans.values() for span in spans):
            raise ValueError("source span is outside the actual understanding input")


class UnderstandingProposal(PreparationModel):
    """Private unconfirmed result, still subject to the owned Core completion fence."""

    operation_id: UUID = Field(alias="operationId")
    draft_id: UUID = Field(alias="draftId")
    draft_revision: int = Field(alias="draftRevision", strict=True, ge=1)
    input_sha256: Sha256 = Field(alias="inputSha256")
    suggestions: DraftSuggestions


class UnderstandingSource(PreparationModel):
    """Immutable public proposal binding; identifiers never confer authorization."""

    draft_id: UUID = Field(alias="draftId")
    draft_revision: int = Field(alias="draftRevision", strict=True, ge=1)
    input_sha256: Sha256 = Field(alias="inputSha256")
    proposal_sha256: Sha256 = Field(alias="proposalSha256")


class PreparationStatusResponse(PreparationModel):
    """Public state only: never serialize a private operation dataclass wholesale."""

    operation_id: UUID = Field(alias="operationId")
    state: Literal["PENDING", "PROPOSED", "MANUAL_CONTINUATION"]
    suggestions: DraftSuggestions | None

    @model_validator(mode="after")
    def proposed_only(self) -> PreparationStatusResponse:
        if (self.state == "PROPOSED") != (self.suggestions is not None):
            raise ValueError("only a proposed operation may expose unconfirmed suggestions")
        return self
