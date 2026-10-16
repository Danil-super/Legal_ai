"""One persisted factual question and an unconfirmed, answer-grounded interpretation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from legal_core.analysis_clarifications import (
    ClarificationRequest,
    ClarificationRequestBatch,
    confirmed_answers,
)
from legal_core.case_preparation_contracts import PreparationModel, Sha256
from legal_core.factual_safety_intake import (
    SCREENING_FIELDS,
    SCREENING_VERSION,
    validate_partial_screening,
)
from legal_core.intake_preparation_contracts import SourceSpan
from legal_core.pseudonymization import pseudonymize_text

AnswerState = Literal[
    "INCOMING", "SITUATION", "SERVICES", "EVENT", "EVENT_DATE", "CHRONOLOGY",
    "CLINIC_ACTIONS", "HEALTH", "SAFETY", "ANALYSIS_CLARIFICATION",
]
AnswerField = Literal[
    "incomingKind", "situationAreas", "affectedServices", "eventSummary", "eventDate",
    "conflictStage", "clinicActions", "healthSignals", "safetyScreening",
    "analysisClarificationAnswers",
]
ScreeningField = Literal[
    "healthDeteriorationReported", "hospitalizationReported", "representativeContact",
    "writtenRequirementsReceived", "authorityOrCourtDocumentReceived",
    "authorityReferralMentioned", "moneyRequested", "amount",
]
_TARGETS: dict[str, str] = dict(zip(
    ("INCOMING", "SITUATION", "SERVICES", "EVENT", "EVENT_DATE", "CHRONOLOGY",
     "CLINIC_ACTIONS", "HEALTH", "SAFETY", "ANALYSIS_CLARIFICATION"),
    ("incomingKind", "situationAreas", "affectedServices", "eventSummary", "eventDate",
     "conflictStage", "clinicActions", "healthSignals", "safetyScreening",
     "analysisClarificationAnswers"), strict=True,
))


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _facts(data: Mapping[str, object]) -> dict[str, object]:
    return {
        "INTAKE_VERSION": "GUIDED_V2", "SITUATION_AREAS": data.get("situationAreas"),
        "AFFECTED_SERVICES": data.get("affectedServices"),
        "ANALYSIS_CLARIFICATION_ANSWERS": data.get("analysisClarificationAnswers", {"answers": []}),
    }


def _service_scope(data: Mapping[str, object]) -> str:
    return _digest({"situationAreas": data.get("situationAreas"),
                    "affectedServices": data.get("affectedServices")})


class AnswerQuestion(PreparationModel):
    schema_version: Literal["intake-answer-question.v1"] = Field(alias="schemaVersion")
    wizard_state: AnswerState = Field(alias="wizardState")
    target_field: AnswerField = Field(alias="targetField")
    screening_field: ScreeningField | None = Field(default=None, alias="screeningField")
    clarification_request: ClarificationRequest | None = Field(
        default=None, alias="clarificationRequest",
    )
    service_scope_sha256: Sha256 | None = Field(default=None, alias="serviceScopeSha256")

    @field_validator("clarification_request", mode="before")
    @classmethod
    def revalidate_request(cls, value: object) -> object:
        return value.model_dump(by_alias=True, warnings=False) if isinstance(
            value, ClarificationRequest,
        ) else value

    @model_validator(mode="after")
    def coherent_target(self) -> Self:
        if _TARGETS[self.wizard_state] != self.target_field:
            raise ValueError("question target must match its persisted wizard state")
        if (self.wizard_state == "SAFETY") != (self.screening_field is not None):
            raise ValueError("only safety questions have a screening field")
        if (self.wizard_state == "ANALYSIS_CLARIFICATION") != (
            self.clarification_request is not None
        ):
            raise ValueError("only clarification questions have a clarification identity")
        scoped = self.wizard_state == "SERVICES" or (
            self.clarification_request is not None
            and self.clarification_request.service_slot is not None
        )
        if scoped != (self.service_scope_sha256 is not None):
            raise ValueError("service questions require the current service scope digest")
        return self

    @property
    def question_sha256(self) -> str:
        return _digest(self.model_dump(mode="json", by_alias=True))


class AnswerInterpretation(PreparationModel):
    status: Literal["PROPOSED", "UNRESOLVED"]
    value: JsonValue | None
    source_spans: list[SourceSpan] = Field(alias="sourceSpans", max_length=3)
    reason_code: Literal["AMBIGUOUS", "CONTRADICTS_CONFIRMED", "OFF_QUESTION"] | None = Field(
        default=None, alias="reasonCode",
    )

    @model_validator(mode="after")
    def coherent_interpretation(self) -> Self:
        if self.status == "PROPOSED":
            if self.value is None or not self.source_spans or self.reason_code is not None:
                raise ValueError("proposed answers require a value and literal source anchors")
        elif self.value is not None or self.reason_code is None:
            raise ValueError("unresolved answers require only a bounded reason, never a value")
        return self

    def validate_source(self, source: str) -> None:
        try:
            validated = AnswerInterpretation.model_validate(
                self.model_dump(by_alias=True, warnings=False),
            )
            if (not isinstance(source, str) or not 1 <= len(source) <= 1500
                    or not source.strip() or pseudonymize_text(source).changed):
                raise ValueError("answer source must be bounded and de-identified")
            if any(span.end > len(source) or not source[span.start:span.end].strip()
                   for span in validated.source_spans):
                raise ValueError("answer anchor is outside the actual answer or blank")
            return
        except (ValueError, TypeError, AttributeError):
            pass
        raise ValueError("invalid answer interpretation or source anchors")


def derive_answer_question(
    draft_data: Mapping[str, object], wizard_state: str,
) -> AnswerQuestion | None:
    """Derive one question from stored facts, never from model-supplied routing metadata."""
    if type(draft_data.get("intakeVersion")) is not int or draft_data["intakeVersion"] != 2:
        raise ValueError("answer interpretation requires a v2 intake draft")
    if wizard_state not in _TARGETS:
        return None
    payload: dict[str, object] = {
        "schemaVersion": "intake-answer-question.v1", "wizardState": wizard_state,
        "targetField": _TARGETS[wizard_state],
    }
    if wizard_state == "SERVICES":
        areas = draft_data.get("situationAreas")
        if not isinstance(areas, list) or "TREATMENT" not in areas:
            raise ValueError("service questions require the treatment area")
        payload["serviceScopeSha256"] = _service_scope(draft_data)
    elif wizard_state == "SAFETY":
        screening = validate_partial_screening(draft_data.get(
            "safetyScreening", {"schemaVersion": SCREENING_VERSION},
        ))
        field = next((item for item in SCREENING_FIELDS if item not in screening), None)
        if field is None and screening.get("moneyRequested") == "YES" and "amount" not in screening:
            field = "amount"
        if field is None and "amount" not in screening:
            # Match the manual adapter's recovery for an incomplete dependent
            # amount after an initial narrative proposal: do not silently infer it.
            field = "moneyRequested"
        if field is None:
            return None
        payload["screeningField"] = field
    elif wizard_state == "ANALYSIS_CLARIFICATION":
        requests = ClarificationRequestBatch.model_validate({
            "requests": draft_data.get("analysisClarificationRequests", []),
        }).requests
        answers = confirmed_answers(_facts(draft_data))
        identities = {(item.question_id, item.service_slot) for item in answers.answers}
        request = next((item for item in requests
                        if (item.question_id, item.service_slot) not in identities), None)
        if request is None:
            return None
        # Scope validation does not allow new service identities or revisiting attempts.
        from legal_core.analysis_clarifications import validate_request_scope

        validate_request_scope([request], _facts(draft_data))
        payload["clarificationRequest"] = request.model_dump(mode="json", by_alias=True)
        if request.service_slot is not None:
            payload["serviceScopeSha256"] = _service_scope(draft_data)
    return AnswerQuestion.model_validate(payload)
