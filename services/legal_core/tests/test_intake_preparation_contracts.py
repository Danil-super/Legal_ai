"""Unconfirmed, bounded understanding; never legal authority or confirmed facts."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from legal_core.intake_preparation_contracts import DraftSuggestions

SOURCE = "Клиника пригласила на осмотр. Пациент сообщает о сколе коронки."


def suggestions():
    return {
        "clinicActions": ["INVITED_FOR_EXAMINATION"],
        "sourceSpans": {"clinicActions": [{"start": 0, "end": 26}]},
    }


def test_only_unconfirmed_existing_fields_with_grounding_survive_roundtrip():
    result = DraftSuggestions.model_validate(suggestions())
    result.validate_source(SOURCE)
    assert result.model_dump(mode="json", by_alias=True, exclude_none=True)["clinicActions"] == [
        "INVITED_FOR_EXAMINATION",
    ]
    assert "confirmed" not in result.model_dump(mode="json", by_alias=True)


@pytest.mark.parametrize("patch", [
    {"riskLevel": "LOW"}, {"legalAdvice": "Недопустимое указание"},
    {"clinicId": "00000000-0000-0000-0000-000000000001"}, {"confirmed": True},
    {"eventSummary": SOURCE}, {"incomingKind": "LEGAL_QUALIFICATION"},
    {"situationAreas": ["ILLEGAL_ENUM"]}, {"affectedServices": ["x" * 121]},
    {"healthSignals": ["NO_KNOWN_INFORMATION", "HOSPITALIZATION"]},
    {"clinicActions": ["NOTHING_YET", "INVITED_FOR_EXAMINATION"]},
    {"clinicActions": ["INVITED_FOR_EXAMINATION", "INVITED_FOR_EXAMINATION"]},
    {"sourceSpans": {}}, {"sourceSpans": {"riskLevel": [{"start": 0, "end": 5}]}},
    {"sourceSpans": {"clinicActions": [{"start": True, "end": 5}]}},
    {"sourceSpans": {"clinicActions": [{"start": 6, "end": 5}]}},
    {"safetyScreening": {"hospitalizationReported": "NO"}},
    {"analysisClarificationAnswers": {"answers": [{"questionId": "FREE_TEXT"}]}},
])
def test_rejects_authority_injection_invalid_values_missing_or_malformed_grounding(patch):
    with pytest.raises(ValidationError):
        DraftSuggestions.model_validate({**suggestions(), **patch})


def test_out_of_bounds_source_span_is_not_grounded():
    payload = suggestions()
    payload["sourceSpans"]["clinicActions"][0]["end"] = len(SOURCE) + 1
    with pytest.raises(ValueError):
        DraftSuggestions.model_validate(payload).validate_source(SOURCE)


def test_explicit_unknown_can_be_proposed_but_not_a_default_negative():
    result = DraftSuggestions.model_validate({
        "conflictStage": "UNKNOWN",
        "sourceSpans": {"conflictStage": [{"start": 0, "end": 10}]},
    })
    assert result.conflict_stage == "UNKNOWN"
    assert result.health_signals is None
    assert result.safety_screening is None


def test_nested_constructed_objects_are_revalidated():
    result = DraftSuggestions.model_validate(suggestions())
    invalid = deepcopy(result)
    invalid.source_spans["clinicActions"][0].start = True
    with pytest.raises((ValidationError, ValueError)):
        DraftSuggestions.model_validate(invalid)
