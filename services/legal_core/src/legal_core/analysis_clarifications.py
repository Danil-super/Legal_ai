"""Bounded factual clarification contracts; never legal evidence or model prose."""

from collections.abc import Mapping
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


def validate_request_scope(
    requests: list[ClarificationRequest], facts: Mapping[str, object]
) -> list[ClarificationRequest]:
    """Revalidate model requests against frozen facts, without inferring answers.

    An explicit UNKNOWN is an attempted answer: asking it again would create an
    automatic retry loop. Missing answers alone may be requested. Tenant selection
    and context fingerprint validation remain responsibilities of the Core route.
    """
    validate_unique_requests(requests)
    if not requests:
        return requests
    if len(requests) > 3 or facts.get("INTAKE_VERSION") != "GUIDED_V2":
        raise ValueError("clarification requests require a bounded guided-v2 batch")
    answers = AnalysisClarificationAnswers.model_validate(
        facts.get("ANALYSIS_CLARIFICATION_ANSWERS", {"answers": []})
    )
    answered = {(item.question_id, item.service_slot) for item in answers.answers}
    services = facts.get("AFFECTED_SERVICES")
    areas = facts.get("SITUATION_AREAS")
    for request in requests:
        if (request.question_id, request.service_slot) in answered:
            raise ValueError("an attempted clarification cannot be requested again")
        if request.question_id == ClarificationQuestionId.RESPONSE_PURPOSE:
            continue
        if (
            not isinstance(areas, list) or "TREATMENT" not in areas
            or not isinstance(services, list) or not 1 <= len(services) <= 5
            or any(not isinstance(item, str) or not 2 <= len(item.strip()) <= 120
                   for item in services)
            or request.service_slot is None or request.service_slot >= len(services)
        ):
            raise ValueError("clarification does not match an existing affected service")
    return requests


def confirmed_answers(facts: Mapping[str, object]) -> AnalysisClarificationAnswers:
    """Read user-statement values and require actual service scope, not just shape."""
    answers = AnalysisClarificationAnswers.model_validate(
        facts.get("ANALYSIS_CLARIFICATION_ANSWERS", {"answers": []})
    )
    if facts.get("INTAKE_VERSION") != "GUIDED_V2":
        raise ValueError("clarification answers require guided-v2 facts")
    services, areas = facts.get("AFFECTED_SERVICES"), facts.get("SITUATION_AREAS")
    for answer in answers.answers:
        if answer.service_slot is None:
            continue
        if (
            not isinstance(areas, list) or "TREATMENT" not in areas
            or not isinstance(services, list) or not 1 <= len(services) <= 5
            or any(not isinstance(item, str) or not 2 <= len(item.strip()) <= 120
                   for item in services)
            or answer.service_slot >= len(services)
        ):
            raise ValueError("answer does not match an existing affected service")
    return answers


def clarification_answers_are_known(facts: Mapping[str, object]) -> bool:
    try:
        answers = confirmed_answers(facts)
    except ValueError:
        return False
    return bool(answers.answers) and all(
        answer.value != "UNKNOWN" if isinstance(answer.value, str)
        else answer.value.precision == "EXACT"
        for answer in answers.answers
    )


def exact_service_dates(facts: Mapping[str, object]) -> tuple[date, ...] | None:
    """The current claim contract has no narrower binding: require each service date.

    Do not assign a single service answer to the other services, substitute the
    event date, or choose a new retrieval anchor. A narrower claim-level service
    selector would require a separate contract rather than guessing text meaning.
    """
    try:
        answers = confirmed_answers(facts)
    except ValueError:
        return None
    services = facts.get("AFFECTED_SERVICES")
    if not isinstance(services, list) or not 1 <= len(services) <= 5:
        return None
    dates = {
        answer.service_slot: date.fromisoformat(answer.value.provided_date)
        for answer in answers.answers
        if isinstance(answer.value, ServiceProvidedDate)
        and answer.value.precision == "EXACT" and answer.value.provided_date is not None
    }
    if any(slot not in dates for slot in range(len(services))):
        return None
    return tuple(dates[slot] for slot in range(len(services)))
