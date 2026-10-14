"""Bounded factual clarification contracts; never legal evidence or model prose."""

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class ClarificationQuestionId(StrEnum):
    SERVICE_PAYMENT_BASIS = "SERVICE_PAYMENT_BASIS"
    SERVICE_PROVIDED_DATE = "SERVICE_PROVIDED_DATE"
    RESPONSE_PURPOSE = "RESPONSE_PURPOSE"


class ClarificationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClarificationRequest(ClarificationModel):
    question_id: ClarificationQuestionId = Field(alias="questionId")
    service_slot: int | None = Field(default=None, alias="serviceSlot", strict=True, ge=0, le=4)

    @model_validator(mode="after")
    def validate_service_scope(self) -> "ClarificationRequest":
        if self.question_id == ClarificationQuestionId.RESPONSE_PURPOSE:
            if self.service_slot is not None:
                raise ValueError("response purpose is global, not service scoped")
        elif self.service_slot is None:
            raise ValueError("service questions require a service slot")
        return self


def validate_unique_requests(requests: list[ClarificationRequest]) -> list[ClarificationRequest]:
    identities = [(item.question_id, item.service_slot) for item in requests]
    if len(identities) != len(set(identities)):
        raise ValueError("clarification identities must be unique")
    return requests


class ClarificationRequestBatch(ClarificationModel):
    requests: list[ClarificationRequest] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_unique(self) -> "ClarificationRequestBatch":
        validate_unique_requests(self.requests)
        return self


class ServiceProvidedDate(ClarificationModel):
    provided_date: str | None = Field(
        alias="date", strict=True, pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    precision: Literal["EXACT", "APPROXIMATE", "UNKNOWN"]

    @model_validator(mode="after")
    def validate_date_precision(self) -> "ServiceProvidedDate":
        if self.precision == "UNKNOWN":
            if self.provided_date is not None:
                raise ValueError("unknown service date cannot carry a date")
        elif self.provided_date is None:
            raise ValueError("exact or approximate service date requires a date")
        else:
            date.fromisoformat(self.provided_date)
        return self


class ClarificationAnswer(ClarificationRequest):
    value: StrictStr | ServiceProvidedDate

    @model_validator(mode="after")
    def validate_answer_kind(self) -> "ClarificationAnswer":
        if self.question_id == ClarificationQuestionId.SERVICE_PROVIDED_DATE:
            if not isinstance(self.value, ServiceProvidedDate):
                raise ValueError("service date requires a bounded date value")
        else:
            choices = (
                {"PAID", "PUBLICLY_COVERED", "OTHER", "UNKNOWN"}
                if self.question_id == ClarificationQuestionId.SERVICE_PAYMENT_BASIS
                else {"RECEIVE_AND_EXPLAIN", "ANSWER_REQUEST", "OTHER", "UNKNOWN"}
            )
            if not isinstance(self.value, str) or self.value not in choices:
                raise ValueError("answer must use the allowed factual choices")
        return self


class AnalysisClarificationAnswers(ClarificationModel):
    answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=11)

    @model_validator(mode="after")
    def validate_unique(self) -> "AnalysisClarificationAnswers":
        validate_unique_requests(list(self.answers))
        return self
