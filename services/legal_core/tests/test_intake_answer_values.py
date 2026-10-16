"""Strict target-only confirmed updates for synthetic free answers."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_answer_values import (
    apply_answer_value,
    preview_answer_value,
    validate_answer_value,
)
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from legal_core.intake_answer_values import validate_answer_shape


@pytest.mark.parametrize(("state", "value", "source"), [
    ("INCOMING", "COMPLAINT", "An unhappy message arrived."),
    ("SITUATION", ["TREATMENT", "SERVICE"], "Both treatment and service."),
    ("SERVICES", ["Restoration"], "Restoration"),
    ("EVENT", "A patient asks about the result.", "A patient asks about the result."),
    ("EVENT_DATE", {"date": None, "precision": "UNKNOWN"}, "Unknown"),
    ("EVENT_DATE", {"date": "2025-01-01", "precision": "APPROXIMATE"}, "Around January 2025"),
    ("CHRONOLOGY", "ONGOING", "This has happened before."),
    ("CLINIC_ACTIONS", ["NOTHING_YET"], "Nothing done yet."),
    ("HEALTH", ["UNKNOWN"], "No information available."),
])
def test_validate_and_apply_only_the_current_question(state, value, source):
    data = {"intakeVersion": 2, "situationAreas": ["TREATMENT"], "unrelated": None}
    data.pop("unrelated")
    before = deepcopy(data)
    question = derive_answer_question(data, state)
    assert validate_answer_value(question, value, source, data) == value
    result = apply_answer_value(data, question, value, source)
    assert result[question.target_field] == value
    assert data == before


@pytest.mark.parametrize(("state", "value"), [
    ("INCOMING", True), ("INCOMING", "legal_claim"),
    ("SITUATION", []), ("SITUATION", ["TREATMENT", "TREATMENT"]),
    ("SERVICES", ["https://example.test"]), ("SERVICES", ["Invented service"]),
    ("SERVICES", ["x"]), ("SERVICES", ["Restoration"] * 2),
    ("EVENT", "A different summary is not the verbatim answer."),
    ("EVENT", "short"), ("EVENT_DATE", {"date": None, "precision": "EXACT"}),
    ("EVENT_DATE", {"date": "2025-01-01", "precision": "UNKNOWN"}),
    ("EVENT_DATE", {"date": (date.today() + timedelta(days=1)).isoformat(), "precision": "EXACT"}),
    ("CLINIC_ACTIONS", ["NOTHING_YET", "OFFERED_REFUND"]),
    ("HEALTH", ["UNKNOWN", "HOSPITALIZATION"]),
    ("HEALTH", ["NO_KNOWN_INFORMATION", "OTHER_CLINIC"]),
])
def test_invalid_proposals_reject_without_input_bearing_exception_context(state, value):
    data = {"intakeVersion": 2, "situationAreas": ["TREATMENT"]}
    question = derive_answer_question(data, state)
    with pytest.raises(ValueError) as caught:
        validate_answer_value(question, value, "Restoration", data)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_answer_source_with_direct_identifiers_is_rejected_not_silently_redacted():
    data = {"intakeVersion": 2}
    question = derive_answer_question(data, "INCOMING")
    with pytest.raises(ValueError):
        validate_answer_value(question, "COMPLAINT", "Contact: patient@example.test", data)


def test_changed_incoming_kind_invalidates_screening_but_not_other_facts():
    data = {"intakeVersion": 2, "incomingKind": "UNKNOWN", "eventSummary": "Synthetic situation.",
            "safetyScreening": {"schemaVersion": SCREENING_VERSION, "moneyRequested": "NO",
                                "amount": "NOT_REQUESTED"}}
    question = derive_answer_question(data, "INCOMING")
    result = apply_answer_value(data, question, "COMPLAINT", "An unhappy message arrived.")
    assert "safetyScreening" not in result
    assert result["eventSummary"] == data["eventSummary"]
    unchanged = apply_answer_value(data, question, "UNKNOWN", "We cannot identify the type.")
    assert unchanged["safetyScreening"] == data["safetyScreening"]


def _service_data():
    return {"intakeVersion": 2, "situationAreas": ["TREATMENT"],
            "affectedServices": ["Restoration", "Extraction"],
            "analysisClarificationRequests": [
                {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
                {"questionId": "RESPONSE_PURPOSE"},
            ], "analysisClarificationAnswers": {"answers": [
                {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "PAID"},
                {"questionId": "RESPONSE_PURPOSE", "value": "UNKNOWN"},
            ]}}


def test_reordering_services_invalidates_all_scoped_answers_preserving_global_purpose():
    data = _service_data()
    question = derive_answer_question(data, "SERVICES")
    result = apply_answer_value(data, question, ["Extraction", "Restoration"],
                                "Extraction; Restoration")
    assert result["analysisClarificationAnswers"]["answers"] == [
        {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "UNKNOWN"},
    ]
    assert result["analysisClarificationRequests"] == [
        {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None},
    ]
    assert len(data["analysisClarificationAnswers"]["answers"]) == 2


def test_removing_treatment_area_removes_service_scope_and_dependent_answers():
    data = _service_data()
    result = apply_answer_value(data, derive_answer_question(data, "SITUATION"),
                                ["SERVICE"], "Only service interaction.")
    assert "affectedServices" not in result
    assert len(result["analysisClarificationAnswers"]["answers"]) == 1


def test_adding_service_area_keeps_existing_treatment_service_answers():
    data = _service_data()
    result = apply_answer_value(data, derive_answer_question(data, "SITUATION"),
                                ["TREATMENT", "SERVICE"], "Treatment and service interaction.")
    assert result["affectedServices"] == data["affectedServices"]
    assert result["analysisClarificationAnswers"] == data["analysisClarificationAnswers"]
    assert result["analysisClarificationRequests"] == data["analysisClarificationRequests"]


def test_changed_health_signals_reopen_screening_just_like_manual_buttons():
    data = {"intakeVersion": 2, "healthSignals": ["UNKNOWN"],
            "safetyScreening": {"schemaVersion": SCREENING_VERSION,
                                "hospitalizationReported": "UNKNOWN"}}
    result = apply_answer_value(data, derive_answer_question(data, "HEALTH"),
                                ["HOSPITALIZATION"], "Hospitalization was reported.")
    assert "safetyScreening" not in result
    assert data["safetyScreening"]["hospitalizationReported"] == "UNKNOWN"


def test_safety_answer_preserves_other_signals_and_invalidates_only_dependent_amount():
    screening = {"schemaVersion": SCREENING_VERSION,
                 **{key: "NO" for key in SCREENING_FIELDS if key != "moneyRequested"}}
    data = {"intakeVersion": 2, "safetyScreening": screening}
    question = derive_answer_question(data, "SAFETY")
    result = apply_answer_value(data, question, "NO", "No monetary request.")
    assert result["safetyScreening"] == {**screening, "moneyRequested": "NO",
                                         "amount": "NOT_REQUESTED"}
    assert data["safetyScreening"] == screening
    data["safetyScreening"]["moneyRequested"] = "YES"
    question = derive_answer_question(data, "SAFETY")
    result = apply_answer_value(data, question, {"amountKopecks": 123456, "currency": "RUB"},
                                "Requested 1234.56 rubles.")
    assert result["safetyScreening"]["amount"]["amountKopecks"] == 123456
    for bad in ({"amountKopecks": True, "currency": "RUB"},
                {"amountKopecks": "123456", "currency": "RUB"}, "NOT_REQUESTED", "1000"):
        with pytest.raises(ValueError):
            validate_answer_value(question, bad, "Amount unknown.", data)


def test_current_clarification_value_not_full_answer_and_unknown_not_repeated():
    data = {"intakeVersion": 2, "situationAreas": ["TREATMENT"],
            "affectedServices": ["Restoration"], "analysisClarificationRequests": [
                {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0},
                {"questionId": "RESPONSE_PURPOSE"},
            ]}
    question = derive_answer_question(data, "ANALYSIS_CLARIFICATION")
    value = {"date": None, "precision": "UNKNOWN"}
    result = apply_answer_value(data, question, value, "Unknown.")
    assert result["analysisClarificationAnswers"]["answers"][0]["value"] == value
    assert "eventDate" not in result
    assert derive_answer_question(result, "ANALYSIS_CLARIFICATION").target_field == (
        "analysisClarificationAnswers"
    )
    current = derive_answer_question(result, "ANALYSIS_CLARIFICATION")
    assert current.clarification_request.question_id == "RESPONSE_PURPOSE"
    with pytest.raises(ValueError):
        apply_answer_value(result, question, "PAID", "It was paid.")


def test_stale_service_question_never_applies_answer_to_a_new_service_slot():
    data = _service_data()
    data.pop("analysisClarificationAnswers")
    question = derive_answer_question(data, "ANALYSIS_CLARIFICATION")
    data["affectedServices"].reverse()
    with pytest.raises(ValueError):
        apply_answer_value(data, question, "PAID", "It was paid.")


def test_shape_rejects_python_values_that_bypass_json_type_boundaries():
    data = {"intakeVersion": 2}
    question = derive_answer_question(data, "SITUATION")
    with pytest.raises(ValueError):
        validate_answer_shape(question, ("TREATMENT",))


def test_frozen_analysis_date_controls_future_date_validation():
    data = {"intakeVersion": 2}
    question = derive_answer_question(data, "EVENT_DATE")
    value = {"date": "2025-02-01", "precision": "EXACT"}
    with pytest.raises(ValueError):
        validate_answer_value(question, value, "2025-02-01", data, as_of_date=date(2025, 1, 1))
    assert validate_answer_value(question, value, "2025-02-01", data,
                                 as_of_date=date(2025, 3, 1)) == value


@pytest.mark.parametrize(("state", "value", "source"), [
    ("INCOMING", "COMPLAINT", "An unhappy message arrived."),
    ("SITUATION", ["SERVICE"], "Only service interaction."),
    ("SERVICES", ["Extraction", "Restoration"], "Extraction; Restoration"),
    ("EVENT", "A patient asks about the result.", "A patient asks about the result."),
    ("EVENT_DATE", {"date": None, "precision": "UNKNOWN"}, "Unknown"),
    ("CHRONOLOGY", "ONGOING", "This has happened before."),
    ("CLINIC_ACTIONS", ["NOTHING_YET"], "Nothing done yet."),
    ("HEALTH", ["HOSPITALIZATION"], "Hospitalization was reported."),
    ("SAFETY", "YES", "Yes."),
    ("ANALYSIS_CLARIFICATION", "UNKNOWN", "Unknown."),
])
def test_preview_uses_exactly_the_same_mutation_for_every_question_family(state, value, source):
    data = _service_data()
    data["healthSignals"] = ["UNKNOWN"]
    data["safetyScreening"] = {"schemaVersion": SCREENING_VERSION, "moneyRequested": "YES"}
    if state == "ANALYSIS_CLARIFICATION":
        data.pop("analysisClarificationAnswers")
    before = deepcopy(data)
    question = derive_answer_question(data, state)
    expected = apply_answer_value(data, question, value, source)
    assert preview_answer_value(data, question, value) == expected
    assert data == before


def test_preview_is_shape_only_and_never_weakens_source_grounding_for_acceptance():
    data = {"intakeVersion": 2, "situationAreas": ["TREATMENT"]}
    question = derive_answer_question(data, "SERVICES")
    result = preview_answer_value(data, question, ["Restoration"])
    assert result["affectedServices"] == ["Restoration"]
    with pytest.raises(ValueError):
        apply_answer_value(data, question, ["Restoration"], "A different service.")


def test_preview_rejects_stale_and_invalid_question_without_private_error_context():
    data = _service_data()
    data.pop("analysisClarificationAnswers")
    question = derive_answer_question(data, "ANALYSIS_CLARIFICATION")
    data["affectedServices"].reverse()
    for candidate in (question, question.model_copy(update={"target_field": "incomingKind"})):
        with pytest.raises(ValueError) as caught:
            preview_answer_value(data, candidate, "PAID")
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None


def test_preview_checks_shape_and_the_same_frozen_date_without_raw_answer():
    data = {"intakeVersion": 2}
    question = derive_answer_question(data, "EVENT_DATE")
    for value in (True, {"date": "2025-02-01", "precision": "EXACT"}):
        with pytest.raises(ValueError):
            preview_answer_value(data, question, value, as_of_date=date(2025, 1, 1))


@pytest.mark.parametrize(("signal", "amount"), [("NO", "NOT_REQUESTED"), ("UNKNOWN", "UNKNOWN")])
def test_preview_reuses_monetary_dependency_and_preserves_other_signals(signal, amount):
    data = {"intakeVersion": 2, "safetyScreening": {
        "schemaVersion": SCREENING_VERSION,
        **{key: "NO" for key in SCREENING_FIELDS if key != "moneyRequested"},
    }}
    question = derive_answer_question(data, "SAFETY")
    result = preview_answer_value(data, question, signal)
    assert result == apply_answer_value(data, question, signal, "A factual answer.")
    assert result["safetyScreening"]["amount"] == amount
    assert result["safetyScreening"]["hospitalizationReported"] == "NO"
