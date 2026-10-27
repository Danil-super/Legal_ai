"""Pure source-bound additions; Core callers still own authorization and persistence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
from uuid import UUID, uuid5

from pydantic import JsonValue

from legal_core.analysis_clarifications import ClarificationRequestBatch, confirmed_answers
from legal_core.contracts import validate_intake_draft_data
from legal_core.factual_safety_intake import (
    SCREENING_FIELDS,
    SCREENING_VERSION,
    validate_partial_screening,
)
from legal_core.intake_answer_contracts import (
    AnswerInterpretation,
    AnswerQuestion,
    FactualContextProposal,
    RetainedSourceSegment,
    derive_answer_question,
)
from legal_core.intake_answer_values import apply_answer_value, validate_grounded_answer_value


def retained_answer_sources(
    operation_id: UUID, revision: int, data: Mapping[str, object], answer: str,
) -> list[RetainedSourceSegment]:
    """Only existing retained narrative and current answer; no files or upload text."""
    entries: list[tuple[str, object]] = [("FOLLOWUP_ANSWER", answer)]
    if "eventSummary" in data:
        entries.insert(0, ("INITIAL_DESCRIPTION", data["eventSummary"]))
    return [RetainedSourceSegment.model_validate({
        "sourceId": uuid5(operation_id, f"{revision}:{kind}"), "kind": kind,
        "revision": revision, "text": text,
        "sourceSha256": hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else "",
    }) for kind, text in entries]


def allowed_factual_questions(
    data: Mapping[str, object], current: AnswerQuestion,
) -> list[AnswerQuestion]:
    """Fill-only target identities, not questions to ask the user or model permissions."""
    validate_intake_draft_data(dict(data))
    if type(data.get("intakeVersion")) is not int or data["intakeVersion"] != 2:
        raise ValueError("factual additions require a v2 draft")
    questions: list[AnswerQuestion] = []
    for state in ("INCOMING", "SITUATION", "EVENT_DATE", "CHRONOLOGY", "CLINIC_ACTIONS", "HEALTH"):
        question = derive_answer_question(data, state)
        if question is not None and question.target_field not in data:
            questions.append(question)
    areas = data.get("situationAreas")
    if isinstance(areas, list) and "TREATMENT" in areas:
        question = derive_answer_question(data, "SERVICES")
        if question is not None and "affectedServices" not in data:
            questions.append(question)
    screening = validate_partial_screening(data.get(
        "safetyScreening", {"schemaVersion": SCREENING_VERSION},
    ))
    fields = [*SCREENING_FIELDS]
    if screening.get("moneyRequested") not in {"NO", "UNKNOWN"}:
        fields.append("amount")
    questions.extend(AnswerQuestion.model_validate({
        "schemaVersion": "intake-answer-question.v1", "wizardState": "SAFETY",
        "targetField": "safetyScreening", "screeningField": field,
    }) for field in fields if field not in screening)
    requests = ClarificationRequestBatch.model_validate({
        "requests": data.get("analysisClarificationRequests", []),
    }).requests
    if current.wizard_state == "SERVICES":
        # These are extraction slots within the primary service proposal, not
        # current service identities. Core binds them after validating its names.
        questions.extend(AnswerQuestion.model_validate({
            "schemaVersion": "intake-answer-question.v1", "wizardState": "ANALYSIS_CLARIFICATION",
            "targetField": "analysisClarificationAnswers", "scopeBasis": "PROPOSED_SERVICES",
            "serviceScopeSha256": current.service_scope_sha256,
            "clarificationRequest": {"questionId": question_id, "serviceSlot": slot},
        }) for slot in range(5)
            for question_id in ("SERVICE_PROVIDED_DATE", "SERVICE_PAYMENT_BASIS"))
    for request in requests:
        if current.wizard_state == "SERVICES" and request.service_slot is not None:
            continue
        question = derive_answer_question({
            **data, "analysisClarificationRequests": [request.model_dump(by_alias=True)],
        }, "ANALYSIS_CLARIFICATION")
        if question is not None:
            questions.append(question)
    return [question for question in questions
            if question.question_sha256 != current.question_sha256]


def validate_factual_proposal(
    proposal: FactualContextProposal, sources: Sequence[RetainedSourceSegment],
    allowed: Sequence[AnswerQuestion], data: Mapping[str, object], *, as_of_date: date,
) -> list[JsonValue]:
    """Return checked values; source offsets never establish semantic truth or authz."""
    validate_intake_draft_data(dict(data))
    parsed = FactualContextProposal.model_validate(proposal.model_dump(by_alias=True))
    parsed.validate_sources(sources)
    identities = {item.source_id: item for item in sources}
    permitted = {item.question_sha256 for item in allowed}
    values: list[JsonValue] = []
    for candidate in parsed.candidates:
        if candidate.question.question_sha256 not in permitted:
            raise ValueError("additional factual target is not permitted")
        # A date/service/amount can have its own anchor in a rich sentence; dates
        # elsewhere in that sentence must not be parsed as monetary amounts.
        literal = " ".join(identities[anchor.source_id].text[anchor.start:anchor.end]
                           for anchor in candidate.anchors)
        values.append(validate_grounded_answer_value(
            candidate.question, candidate.value, literal, as_of_date=as_of_date,
            require_explicit_currency=True,
        ))
    return values


def validate_retained_answer_sources(
    data: Mapping[str, object], answer: str, sources: Sequence[RetainedSourceSegment],
) -> list[RetainedSourceSegment]:
    """Check the currently supported two-source bundle before any model I/O."""
    checked = [RetainedSourceSegment.model_validate(item.model_dump(
        by_alias=True, warnings="error")) for item in sources]
    latest = [item for item in checked if item.kind == "FOLLOWUP_ANSWER"]
    narrative = [item for item in checked if item.kind == "INITIAL_DESCRIPTION"]
    if (not 1 <= len(checked) <= 2 or len(latest) != 1 or latest[0].text != answer
            or len({item.revision for item in checked}) != 1
            or len({item.source_id for item in checked}) != len(checked)
            or len(narrative) != int("eventSummary" in data)
            or any(item.text != data["eventSummary"] for item in narrative)):
        raise ValueError("retained source binding does not match current draft and answer")
    return checked


def apply_factual_answer(
    data: Mapping[str, object], current: AnswerQuestion, interpretation: AnswerInterpretation,
    answer: str, sources: Sequence[RetainedSourceSegment], *, as_of_date: date,
) -> dict[str, object]:
    """All-or-nothing provisional merge. Only the current target may be corrected."""
    parsed = AnswerInterpretation.model_validate(interpretation.model_dump(by_alias=True))
    parsed.validate_source(answer)
    if parsed.status != "PROPOSED":
        raise ValueError("current answer has not been resolved")
    result = apply_answer_value(data, current, parsed.value, answer, as_of_date=as_of_date)
    proposal = parsed.factual_proposal
    if proposal is None:
        return result
    checked_sources = validate_retained_answer_sources(data, answer, sources)
    allowed = allowed_factual_questions(data, current)
    values = validate_factual_proposal(proposal, checked_sources, allowed, data,
                                       as_of_date=as_of_date)
    still_allowed = {item.question_sha256 for item in allowed_factual_questions(result, current)}
    sources_by_id = {item.source_id: item for item in checked_sources}
    for candidate, value in zip(proposal.candidates, values, strict=True):
        question, field = candidate.question, candidate.question.target_field
        if question.question_sha256 not in still_allowed:
            raise ValueError("current answer changed the additional fact's scope")
        if question.scope_basis == "PROPOSED_SERVICES":
            request = question.clarification_request
            services = result.get("affectedServices")
            if (current.wizard_state != "SERVICES" or request is None
                    or request.service_slot is None or not isinstance(services, list)
                    or request.service_slot >= len(services)):
                raise ValueError("proposed service slot has no validated primary service")
            label = services[request.service_slot]
            if len(candidate.anchors) != 1:
                raise ValueError("proposed service fact requires one complete source quote")
            anchor = candidate.anchors[0]
            if (not isinstance(label, str)
                    or label not in sources_by_id[anchor.source_id].text[anchor.start:anchor.end]):
                raise ValueError("additional service fact must anchor its own literal service")
            # Re-derive against the validated proposed list. An old attempt (even
            # UNKNOWN) makes this None and cannot be silently overwritten.
            rebound = derive_answer_question({
                **result, "analysisClarificationRequests": [request.model_dump(by_alias=True)],
            }, "ANALYSIS_CLARIFICATION")
            if rebound is None or rebound.clarification_request != request:
                raise ValueError("additional service fact repeats an existing attempt")
        if field == "safetyScreening":
            raw = result.get(field, {"schemaVersion": SCREENING_VERSION})
            if not isinstance(raw, dict):
                raise ValueError("invalid screening container")
            # Interdependent facts are one batch; validate coherence after all
            # additions, not halfway through amount-before-signal ordering.
            screening = dict(raw)
            if question.screening_field is None:
                raise ValueError("screening identity missing")
            screening[question.screening_field] = value
            if question.screening_field == "moneyRequested" and value in ("NO", "UNKNOWN"):
                screening.setdefault("amount", "NOT_REQUESTED" if value == "NO" else "UNKNOWN")
            result[field] = screening
        elif field == "analysisClarificationAnswers":
            if question.clarification_request is None:
                raise ValueError("clarification identity missing")
            existing = result.get(field, {"answers": []})
            if not isinstance(existing, dict) or not isinstance(existing.get("answers"), list):
                raise ValueError("invalid clarification container")
            result[field] = {"answers": [*existing["answers"], {
                **question.clarification_request.model_dump(mode="json", by_alias=True),
                "value": deepcopy(value),
            }]}
        else:
            result[field] = deepcopy(value)
            if field == "incomingKind":
                result.setdefault("incomingSourceStatus", "NOT_ATTACHED")
    validate_intake_draft_data(result)
    if "safetyScreening" in result:
        validate_partial_screening(result["safetyScreening"])
    confirmed_answers({
        "INTAKE_VERSION": "GUIDED_V2", "SITUATION_AREAS": result.get("situationAreas"),
        "AFFECTED_SERVICES": result.get("affectedServices"),
        "ANALYSIS_CLARIFICATION_ANSWERS": result.get(
            "analysisClarificationAnswers", {"answers": []},
        ),
    })
    return result
