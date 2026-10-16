"""Strict one-question values and explicit confirmed updates, with no I/O or ORM."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from typing import cast

from pydantic import JsonValue

from legal_core.analysis_clarifications import (
    AnalysisClarificationAnswers,
    ClarificationAnswer,
    ServiceProvidedDate,
    confirmed_answers,
)
from legal_core.contracts import validate_intake_draft_data
from legal_core.factual_safety_intake import (
    SCREENING_VERSION,
    RequestedMoney,
    validate_partial_screening,
)
from legal_core.intake_answer_contracts import AnswerQuestion, derive_answer_question
from legal_core.intake_preparation_contracts import DraftSuggestions
from legal_core.intake_service_scope import invalidate_service_clarification_answers
from legal_core.pseudonymization import pseudonymize_text

_LINK = re.compile(r"//|[a-z][a-z0-9+.-]*:|www\.|(?:[\w-]+\.)+[^\W\d_]{2,63}\b", re.I)


def _question(question: AnswerQuestion) -> AnswerQuestion:
    return AnswerQuestion.model_validate(
        question.model_dump(mode="python", by_alias=True, warnings=False),
    )


def _date_not_future(value: ServiceProvidedDate, as_of_date: date | None) -> None:
    if value.provided_date is not None and date.fromisoformat(value.provided_date) > (
        as_of_date or date.today()
    ):
        raise ValueError("future factual date")


def _shape(question: AnswerQuestion, value: object, as_of_date: date | None) -> JsonValue:
    question = _question(question)
    field = question.target_field
    if (field in {"situationAreas", "affectedServices", "clinicActions", "healthSignals"}
            and not isinstance(value, list)):
        raise ValueError("multi-valued answers require a JSON list")
    if field == "eventDate" and not isinstance(value, dict):
        raise ValueError("factual dates require a JSON object")
    if field == "eventSummary":
        if not isinstance(value, str) or not 10 <= len(value.strip()) <= 1500:
            raise ValueError("event answer must be bounded text")
        normalized: JsonValue = value
    elif field == "safetyScreening":
        if question.screening_field == "amount":
            if value != "UNKNOWN" and not isinstance(value, dict):
                raise ValueError("amount requires a JSON object or explicit UNKNOWN")
            normalized = "UNKNOWN" if value == "UNKNOWN" else RequestedMoney.model_validate(
                value,
            ).model_dump(mode="json", by_alias=True)
        else:
            if not isinstance(value, str) or value not in {"YES", "NO", "UNKNOWN"}:
                raise ValueError("safety answer must be an explicit factual signal")
            normalized = value
    elif field == "analysisClarificationAnswers":
        if question.clarification_request is None:
            raise ValueError("clarification identity is absent")
        if (question.clarification_request.question_id == "SERVICE_PROVIDED_DATE"
                and not isinstance(value, dict)):
            raise ValueError("service dates require a JSON object")
        answer = ClarificationAnswer.model_validate({
            **question.clarification_request.model_dump(by_alias=True), "value": value,
        })
        if isinstance(answer.value, ServiceProvidedDate):
            _date_not_future(answer.value, as_of_date)
        normalized = cast(JsonValue, answer.model_dump(mode="json", by_alias=True)["value"])
    else:
        suggestion = DraftSuggestions.model_validate({
            field: value, "sourceSpans": {field: [{"start": 0, "end": 1}]},
        })
        normalized = cast(JsonValue, suggestion.model_dump(mode="json", by_alias=True)[field])
        if field == "eventDate" and suggestion.event_date is not None:
            _date_not_future(suggestion.event_date, as_of_date)
        if field == "affectedServices" and any(
            len(service.strip()) < 2 or _LINK.search(service)
            for service in suggestion.affected_services or []
        ):
            raise ValueError("service names must be non-URL factual labels")
    if pseudonymize_text(json.dumps(normalized, ensure_ascii=False)).changed:
        raise ValueError("answer value contains direct identifiers")
    return normalized


def validate_answer_shape(
    question: AnswerQuestion, value: object, *, as_of_date: date | None = None,
) -> JsonValue:
    """Validate a public value's closed type; not source grounding or authorization."""
    try:
        return _shape(question, value, as_of_date)
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError("answer does not match the current factual question")


def validate_answer_value(
    question: AnswerQuestion, value: object, source: str, draft_data: Mapping[str, object],
    *, as_of_date: date | None = None,
) -> JsonValue:
    """Only the actual answer supplies anchors, never another draft field or model memory."""
    try:
        validated = _question(question)
        current = derive_answer_question(draft_data, validated.wizard_state)
        if current is None or current.question_sha256 != validated.question_sha256:
            raise ValueError("question is not current")
        if (not isinstance(source, str) or not 1 <= len(source) <= 1500
                or not source.strip() or pseudonymize_text(source).changed):
            raise ValueError("answer source must be bounded and de-identified")
        normalized = _shape(validated, value, as_of_date)
        if validated.target_field == "eventSummary" and normalized != source:
            raise ValueError("event narrative must be the verbatim user answer")
        if validated.target_field == "affectedServices" and (
            not isinstance(normalized, list) or any(
                not isinstance(service, str) or service not in source for service in normalized
            )
        ):
            raise ValueError("service labels must be literal parts of the actual answer")
        return normalized
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError("answer does not match the current factual question and source")


def _apply(
    data: Mapping[str, object], question: AnswerQuestion, value: object, source: str,
    as_of_date: date | None,
) -> dict[str, object]:
    normalized = validate_answer_value(question, value, source, data, as_of_date=as_of_date)
    validated = _question(question)
    result = deepcopy(dict(data))
    validate_intake_draft_data(result)
    field = validated.target_field
    if field == "safetyScreening":
        screening = dict(validate_partial_screening(result.get(
            field, {"schemaVersion": SCREENING_VERSION},
        )))
        if validated.screening_field is None:
            raise ValueError("screening identity is absent")
        screening[validated.screening_field] = normalized
        if validated.screening_field == "moneyRequested":
            screening.pop("amount", None)
            if normalized in ("NO", "UNKNOWN"):
                screening["amount"] = "NOT_REQUESTED" if normalized == "NO" else "UNKNOWN"
        validate_partial_screening(screening)
        result[field] = screening
    elif field == "analysisClarificationAnswers":
        if validated.clarification_request is None:
            raise ValueError("clarification identity is absent")
        existing = AnalysisClarificationAnswers.model_validate(result.get(field, {"answers": []}))
        added = ClarificationAnswer.model_validate({
            **validated.clarification_request.model_dump(by_alias=True), "value": normalized,
        })
        result[field] = AnalysisClarificationAnswers(
            answers=[*existing.answers, added],
        ).model_dump(mode="json", by_alias=True)
    else:
        if result.get(field) != normalized and field in {"incomingKind", "healthSignals"}:
            result.pop("safetyScreening", None)
        if field == "affectedServices":
            result = invalidate_service_clarification_answers(result, cast(list[str], normalized))
        elif (field == "situationAreas" and isinstance(normalized, list)
              and "TREATMENT" not in normalized):
            result = invalidate_service_clarification_answers(result, None)
        result[field] = deepcopy(normalized)
    validate_intake_draft_data(result)
    confirmed_answers({
        "INTAKE_VERSION": "GUIDED_V2", "SITUATION_AREAS": result.get("situationAreas"),
        "AFFECTED_SERVICES": result.get("affectedServices"),
        "ANALYSIS_CLARIFICATION_ANSWERS": result.get(
            "analysisClarificationAnswers", {"answers": []},
        ),
    })
    return result


def apply_answer_value(
    data: Mapping[str, object], question: AnswerQuestion, value: object, source: str,
    *, as_of_date: date | None = None,
) -> dict[str, object]:
    """Return a new partial draft after explicit consent; never persist or confirm it here.

    Replacements are limited to the current question and its canonical dependencies.
    This does not weaken the separate initial-narrative fill-only merge.
    """
    try:
        return _apply(data, question, value, source, as_of_date)
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError("answer cannot be applied to this draft")
