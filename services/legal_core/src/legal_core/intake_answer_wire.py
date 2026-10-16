"""Dedicated answer wire contract; no arbitrary client question or fact mutation."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from legal_core.case_preparation_contracts import PreparationModel, Sha256
from legal_core.intake_answer_contracts import AnswerInterpretation, AnswerQuestion
from legal_core.intake_preparation_contracts import UnderstandingAcceptance, UnderstandingSource

CONTEXT_FIELDS = frozenset({
    "intakeVersion", "incomingKind", "incomingSourceStatus", "situationAreas", "affectedServices",
    "eventSummary", "eventDate", "conflictStage", "clinicActions", "clinicActionsNote",
    "healthSignals", "caseMaterialsStatus", "safetyScreening", "analysisClarificationAnswers",
})


class StartAnswerRequest(PreparationModel):
    expected_revision: int = Field(alias="expectedRevision", strict=True, ge=1)
    answer: str = Field(strict=True, min_length=1, max_length=1500)


class AnswerStatusResponse(PreparationModel):
    operation_id: UUID = Field(alias="operationId")
    state: Literal["PENDING", "PROPOSED", "UNRESOLVED", "MANUAL_CONTINUATION", "ACCEPTED"]
    question: AnswerQuestion | None = None
    interpretation: AnswerInterpretation | None = None
    source: UnderstandingSource | None = None
    acceptance: UnderstandingAcceptance | None = None

    @model_validator(mode="after")
    def coherent_state(self) -> "AnswerStatusResponse":
        has_result = self.state in {"PROPOSED", "UNRESOLVED"}
        if any(has_result != (value is not None)
               for value in (self.question, self.interpretation, self.source)):
            raise ValueError("only a completed unconfirmed answer exposes its bound interpretation")
        if self.interpretation is not None and self.interpretation.status != self.state:
            raise ValueError("answer state must match its explicit interpretation outcome")
        if (self.state == "ACCEPTED") != (self.acceptance is not None):
            raise ValueError("only accepted facts may expose an acceptance receipt")
        return self


class AnswerDiscoveryResponse(PreparationModel):
    operation: AnswerStatusResponse | None


class AnswerContext(PreparationModel):
    """Private bounded context; no attachments, source URLs, keys or routing authority."""

    operation_id: UUID = Field(alias="operationId")
    draft_id: UUID = Field(alias="draftId")
    draft_revision: int = Field(alias="draftRevision", strict=True, ge=1)
    input_sha256: Sha256 = Field(alias="inputSha256")
    answer_sha256: Sha256 = Field(alias="answerSha256")
    question_sha256: Sha256 = Field(alias="questionSha256")
    as_of_date: date = Field(alias="asOfDate")
    question: AnswerQuestion
    answer: str = Field(strict=True, min_length=1, max_length=1500)
    confirmed_context: dict[str, JsonValue] = Field(alias="confirmedContext", max_length=14)

    @model_validator(mode="after")
    def context_allowlist(self) -> "AnswerContext":
        if not set(self.confirmed_context) <= CONTEXT_FIELDS:
            raise ValueError("answer context contains unsupported fields")
        if self.confirmed_context.get("intakeVersion") != 2:
            raise ValueError("answer context requires guided-v2 facts")
        if self.question.question_sha256 != self.question_sha256:
            raise ValueError("question binding does not match its identity")
        return self


class AnswerProposal(PreparationModel):
    operation_id: UUID = Field(alias="operationId")
    draft_id: UUID = Field(alias="draftId")
    draft_revision: int = Field(alias="draftRevision", strict=True, ge=1)
    input_sha256: Sha256 = Field(alias="inputSha256")
    answer_sha256: Sha256 = Field(alias="answerSha256")
    question_sha256: Sha256 = Field(alias="questionSha256")
    interpretation: AnswerInterpretation
