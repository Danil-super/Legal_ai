"""Canonical existing service-slot invalidation, shared by both intake adapters."""

from collections.abc import Mapping

from legal_core.analysis_clarifications import (
    AnalysisClarificationAnswers,
    ClarificationRequest,
    ClarificationRequestBatch,
)


def _valid_services(value: object) -> bool:
    # Preserve the existing manual intake's legacy limit. Model proposals have
    # their separate stricter 120-character field bound before calling this helper.
    return (
        isinstance(value, list) and 1 <= len(value) <= 5
        and all(isinstance(item, str) and 2 <= len(item.strip()) <= 500 for item in value)
        and len(value) == len(set(value))
    )


def _requests(data: Mapping[str, object]) -> list[ClarificationRequest]:
    raw = data.get("analysisClarificationRequests", [])
    if not isinstance(raw, list) or any(
        not isinstance(item, dict) or "questionId" not in item
        or not set(item) <= {"questionId", "serviceSlot"} for item in raw
    ):
        return []
    try:
        return ClarificationRequestBatch.model_validate({"requests": raw}).requests
    except ValueError:
        return []


def invalidate_service_clarification_answers(
    data: Mapping[str, object], services: list[str] | None,
) -> dict[str, object]:
    """Replace/remove services; changed positional identities discard scoped attempts.

    Global response purpose survives, including its explicit UNKNOWN attempt.
    Input and unrelated draft fields are not mutated. This preserves the manual
    adapter's pre-existing validation and optional-key serialization semantics.
    """
    if services is not None and not _valid_services(services):
        raise ValueError("Укажите от одной до пяти услуг.")
    updated = dict(data)
    if services is None:
        updated.pop("affectedServices", None)
    else:
        updated["affectedServices"] = list(services)
    if services != data.get("affectedServices") and "analysisClarificationAnswers" in data:
        answers = AnalysisClarificationAnswers.model_validate(data["analysisClarificationAnswers"])
        existing_services = data.get("affectedServices")
        for answer in answers.answers:
            if answer.service_slot is not None and (
                not _valid_services(existing_services)
                or not isinstance(existing_services, list)
                or answer.service_slot >= len(existing_services)
            ):
                raise ValueError("clarification answer requires an existing service")
        updated["analysisClarificationAnswers"] = AnalysisClarificationAnswers(
            answers=[answer for answer in answers.answers if answer.service_slot is None],
        ).model_dump(mode="json", by_alias=True)
    if services != data.get("affectedServices") and "analysisClarificationRequests" in data:
        updated["analysisClarificationRequests"] = [
            request.model_dump(mode="json", by_alias=True)
            for request in _requests(data) if request.service_slot is None
        ]
    return updated
