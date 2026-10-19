"""Pure all-or-nothing acceptance merge; never confirms facts or creates a case."""

from copy import deepcopy
from datetime import date

from legal_core.analysis_clarifications import ServiceProvidedDate, confirmed_answers
from legal_core.case_api import ApiError
from legal_core.contracts import validate_intake_draft_data
from legal_core.factual_safety_intake import validate_partial_screening
from legal_core.intake_preparation_contracts import DraftSuggestions


def _insert_absent(target: dict[str, object], key: str, value: object) -> None:
    if key in target and target[key] != value:
        raise ValueError("existing factual attempt conflicts")
    if key not in target:
        target[key] = deepcopy(value)


def _merge_answers(existing: object, proposed: object) -> dict[str, object]:
    if not isinstance(existing, dict) or not isinstance(proposed, dict):
        raise ValueError("invalid clarification container")
    current, additions = existing.get("answers"), proposed.get("answers")
    if not isinstance(current, list) or not isinstance(additions, list):
        raise ValueError("invalid clarification list")
    result = deepcopy(current)
    for answer in additions:
        if not isinstance(answer, dict):
            raise ValueError("invalid clarification answer")
        identity = (answer.get("questionId"), answer.get("serviceSlot"))
        matches = [item for item in result if isinstance(item, dict)
                   and (item.get("questionId"), item.get("serviceSlot")) == identity]
        if matches:
            # The optional global serviceSlot may be absent in an older saved answer.
            if {**matches[0], "serviceSlot": matches[0].get("serviceSlot")} != answer:
                raise ValueError("existing clarification attempt conflicts")
        else:
            result.append(deepcopy(answer))
    return {"answers": result}


def _merge(
    current_data: dict[str, object], suggestions: DraftSuggestions, source: str,
) -> dict[str, object]:
    validate_intake_draft_data(current_data)
    if type(current_data.get("intakeVersion")) is not int or current_data["intakeVersion"] != 2:
        raise ValueError("understanding requires a guided-v2 draft")
    # Plain Python preserves strict booleans and forces nested mutable models back
    # through their validators. Root-only omission retains UNKNOWN's required null.
    validated = DraftSuggestions.model_validate(
        suggestions.model_dump(mode="python", by_alias=True, warnings=False),
    )
    validated.validate_source(source)
    proposed = {key: value for key, value in validated.model_dump(
        mode="json", by_alias=True,
    ).items() if value is not None and key != "sourceSpans"}
    result = deepcopy(current_data)
    for key, value in proposed.items():
        if key == "safetyScreening" and key in result:
            existing = result[key]
            if not isinstance(existing, dict) or not isinstance(value, dict):
                raise ValueError("invalid factual screening")
            for member, signal in value.items():
                _insert_absent(existing, member, signal)
        elif key == "analysisClarificationAnswers" and key in result:
            result[key] = _merge_answers(result[key], value)
        else:
            _insert_absent(result, key, value)
    validate_intake_draft_data(result)
    if "safetyScreening" in result:
        validate_partial_screening(result["safetyScreening"])
    answers = confirmed_answers({
        "INTAKE_VERSION": "GUIDED_V2",
        "SITUATION_AREAS": result.get("situationAreas"),
        "AFFECTED_SERVICES": result.get("affectedServices"),
        "ANALYSIS_CLARIFICATION_ANSWERS": result.get(
            "analysisClarificationAnswers", {"answers": []},
        ),
    })
    dates = [answer.value for answer in answers.answers
             if isinstance(answer.value, ServiceProvidedDate)]
    if "eventDate" in result:
        dates.append(ServiceProvidedDate.model_validate(result["eventDate"]))
    if any(item.provided_date is not None and date.fromisoformat(item.provided_date) > date.today()
           for item in dates):
        raise ValueError("future factual date")
    return result


def merge_preparation_suggestions(
    current_data: dict[str, object], suggestions: DraftSuggestions, source: str,
) -> dict[str, object]:
    """Fill only missing values after explicit consent; conflicting attempts need editing.

    Ownership, operation/proposal binding, locks and revision writes belong to the
    caller. Invalid external proposals never expose validation inputs or chains.
    """
    try:
        return _merge(current_data, suggestions, source)
    except (ValueError, TypeError):
        pass
    # Raise outside the handler: suppressed display alone still retains raw context.
    raise ApiError(
        status_code=409, code="PREPARATION_MERGE_CONFLICT",
        message="Предложение противоречит сохранённым ответам. Откройте редактирование.",
    )
