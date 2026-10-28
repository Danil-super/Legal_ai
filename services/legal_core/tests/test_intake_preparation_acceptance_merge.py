"""Explicit proposal acceptance only fills absent, compatible factual fields."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from legal_core.case_api import ApiError
from legal_core.intake_preparation_contracts import DraftSuggestions, SourceSpan
from legal_core.intake_preparation_merge import merge_preparation_suggestions

SOURCE = "Пациент сообщил о сколе коронки. Клиника пригласила на осмотр."
VERSION = "factual-safety-intake.v1"


def proposal(**fields):
    return DraftSuggestions.model_validate({
        **fields,
        "sourceSpans": {key: [{"start": 0, "end": 10}] for key in fields},
    })


def draft(**fields):
    return {"intakeVersion": 2, "eventSummary": SOURCE, **fields}


def answers(*values):
    return {"answers": list(values)}


def payment(value="PAID", slot=0):
    return {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": slot, "value": value}


def provided(value=None, precision="UNKNOWN", slot=0):
    return {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": slot,
            "value": {"date": value, "precision": precision}}


def assert_conflict(current, suggested, source=SOURCE):
    before = deepcopy(current)
    with pytest.raises(ApiError) as caught:
        merge_preparation_suggestions(current, suggested, source)
    assert current == before
    assert caught.value.status_code == 409
    assert caught.value.code == "PREPARATION_MERGE_CONFLICT"
    assert caught.value.details == {}
    assert SOURCE not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_absent_fields_are_inserted_without_source_spans_or_mutating_inputs():
    current = draft(correctionSource={
        "caseId": "00000000-0000-0000-0000-000000000001", "factSnapshotSha256": "a" * 64,
    })
    suggested = proposal(incomingKind="COMPLAINT", clinicActions=["INVITED_FOR_EXAMINATION"])
    before, before_proposal = deepcopy(current), suggested.model_dump()
    result = merge_preparation_suggestions(current, suggested, SOURCE)
    assert result == {**current, "incomingKind": "COMPLAINT",
                      "clinicActions": ["INVITED_FOR_EXAMINATION"]}
    assert "sourceSpans" not in result
    result["correctionSource"]["factSnapshotSha256"] = "b" * 64
    assert current == before
    assert suggested.model_dump() == before_proposal


@pytest.mark.parametrize("fields", [
    {"incomingKind": "UNKNOWN"}, {"conflictStage": "UNKNOWN"},
    {"clinicActions": ["NOTHING_YET"]}, {"healthSignals": ["UNKNOWN"]},
    {"eventDate": {"date": None, "precision": "UNKNOWN"}},
    {"eventDate": {"date": "2020-01-01", "precision": "APPROXIMATE"}},
    {"analysisClarificationAnswers": {"answers": []}},
])
def test_equal_attempts_are_preserved_including_nested_null_and_valid_empty(fields):
    current = draft(**fields)
    assert merge_preparation_suggestions(current, proposal(**fields), SOURCE) == current


@pytest.mark.parametrize("existing,suggested", [
    ({"incomingKind": "UNKNOWN"}, {"incomingKind": "COMPLAINT"}),
    ({"conflictStage": "UNKNOWN"}, {"conflictStage": "FIRST"}),
    ({"eventDate": {"date": None, "precision": "UNKNOWN"}},
     {"eventDate": {"date": "2020-01-01", "precision": "EXACT"}}),
    ({"eventDate": {"date": "2020-01-01", "precision": "APPROXIMATE"}},
     {"eventDate": {"date": "2020-01-01", "precision": "EXACT"}}),
    ({"clinicActions": ["NOTHING_YET"]}, {"clinicActions": ["OFFERED_REFUND"]}),
    ({"affectedServices": ["Коронка", "Пломба"]},
     {"affectedServices": ["Пломба", "Коронка"]}),
    ({"affectedServices": ["Коронка"]}, {"affectedServices": ["Протезирование"]}),
])
def test_conflicting_existing_top_level_value_rejects_entire_merge(existing, suggested):
    assert_conflict(draft(**existing), proposal(**suggested, incomingKind="COMPLAINT")
                    if "incomingKind" not in suggested else proposal(**suggested))


def test_safety_screening_merges_only_missing_keys_without_inferring_negative_signals():
    current = draft(safetyScreening={"schemaVersion": VERSION, "hospitalizationReported": "NO"})
    result = merge_preparation_suggestions(current, proposal(safetyScreening={
        "schemaVersion": VERSION, "representativeContact": "UNKNOWN",
        "moneyRequested": "NO", "amount": "NOT_REQUESTED",
    }), SOURCE)
    assert result["safetyScreening"] == {
        "schemaVersion": VERSION, "hospitalizationReported": "NO",
        "representativeContact": "UNKNOWN", "moneyRequested": "NO", "amount": "NOT_REQUESTED",
    }


@pytest.mark.parametrize("existing,suggested", [
    ({"hospitalizationReported": "NO"}, {"hospitalizationReported": "YES"}),
    ({"hospitalizationReported": "UNKNOWN"}, {"hospitalizationReported": "NO"}),
    ({"moneyRequested": "NO", "amount": "NOT_REQUESTED"},
     {"moneyRequested": "YES", "amount": "UNKNOWN"}),
])
def test_existing_safety_attempts_cannot_be_overwritten(existing, suggested):
    assert_conflict(draft(safetyScreening={"schemaVersion": VERSION, **existing}),
                    proposal(safetyScreening={"schemaVersion": VERSION, **suggested}))


def test_preexisting_invalid_monetary_combination_is_not_silently_accepted():
    assert_conflict(draft(safetyScreening={
        "schemaVersion": VERSION, "moneyRequested": "NO", "amount": "UNKNOWN",
    }), proposal(incomingKind="COMPLAINT"))


def test_answers_merge_by_identity_preserving_unknown_and_not_inferring_service_date():
    current = draft(situationAreas=["TREATMENT"], affectedServices=["Коронка"],
                    analysisClarificationAnswers=answers(payment("UNKNOWN")),
                    eventDate={"date": "2020-01-01", "precision": "EXACT"})
    result = merge_preparation_suggestions(current, proposal(
        analysisClarificationAnswers=answers(payment("UNKNOWN"), provided())), SOURCE)
    assert result["analysisClarificationAnswers"] == answers(payment("UNKNOWN"), provided())
    assert result["eventDate"] == current["eventDate"]


def test_global_answer_is_allowed_without_manufacturing_service_scope():
    value = {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "UNKNOWN"}
    result = merge_preparation_suggestions(draft(), proposal(
        analysisClarificationAnswers=answers(value)), SOURCE)
    assert result["analysisClarificationAnswers"] == answers(value)
    assert "affectedServices" not in result


@pytest.mark.parametrize("existing,new", [
    (payment("UNKNOWN"), payment()),
    (provided(), provided("2020-01-01", "EXACT")),
    (provided("2020-01-01", "APPROXIMATE"), provided("2020-01-01", "EXACT")),
])
def test_answer_conflict_rejects_every_other_proposed_field(existing, new):
    assert_conflict(draft(situationAreas=["TREATMENT"], affectedServices=["Коронка"],
                          analysisClarificationAnswers=answers(existing)),
                    proposal(incomingKind="COMPLAINT", analysisClarificationAnswers=answers(new)))


@pytest.mark.parametrize("fields,answer", [
    ({}, payment()),
    ({"situationAreas": ["SERVICE"], "affectedServices": ["Коронка"]}, payment()),
    ({"situationAreas": ["TREATMENT"], "affectedServices": ["Коронка"]}, payment(slot=1)),
])
def test_service_answer_requires_effective_treatment_service_scope(fields, answer):
    assert_conflict(draft(**fields), proposal(analysisClarificationAnswers=answers(answer)))


def test_new_service_and_its_answers_can_be_accepted_together():
    result = merge_preparation_suggestions(draft(), proposal(
        situationAreas=["TREATMENT"], affectedServices=["Коронка"],
        analysisClarificationAnswers=answers(payment(), provided())), SOURCE)
    assert result["analysisClarificationAnswers"] == answers(payment(), provided())


def test_existing_answers_are_rechecked_against_effective_service_scope():
    assert_conflict(draft(analysisClarificationAnswers=answers(payment(slot=1))), proposal(
        situationAreas=["TREATMENT"], affectedServices=["Коронка"]))


@pytest.mark.parametrize("kind", ["event", "service"])
def test_future_dates_are_rejected_instead_of_saved_for_later_analysis(kind):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    fields = ({"eventDate": {"date": tomorrow, "precision": "EXACT"}}
              if kind == "event" else {
                  "situationAreas": ["TREATMENT"], "affectedServices": ["Коронка"],
                  "analysisClarificationAnswers": answers(provided(tomorrow, "EXACT")),
              })
    assert_conflict(draft(), proposal(**fields))


def test_invalid_mutated_nested_suggestion_is_revalidated_and_error_chain_is_private():
    suggested = proposal(analysisClarificationAnswers=answers(provided()))
    suggested.analysis_clarification_answers.answers[0].value.provided_date = SOURCE
    assert_conflict(draft(situationAreas=["TREATMENT"], affectedServices=["Коронка"]), suggested)


def test_mutated_boolean_service_slot_is_not_coerced_to_an_integer():
    suggested = proposal(analysisClarificationAnswers=answers(payment()))
    suggested.analysis_clarification_answers.answers[0].service_slot = True
    assert_conflict(draft(situationAreas=["TREATMENT"], affectedServices=["Коронка"]), suggested)


def test_out_of_bounds_span_and_direct_identifiers_are_rejected():
    suggested = proposal(incomingKind="COMPLAINT")
    suggested.source_spans["incomingKind"][0].end = len(SOURCE) + 1
    assert_conflict(draft(), suggested)


def test_unknown_fields_and_incompatible_intake_version_do_not_merge():
    assert_conflict({"problem_summary": SOURCE}, proposal(incomingKind="COMPLAINT"))
    assert_conflict(draft(unsupported="unexpected"), proposal(incomingKind="COMPLAINT"))


def test_empty_proposal_is_a_fresh_copy_without_implicit_factual_changes():
    current = draft(safetyScreening={"schemaVersion": VERSION})
    result = merge_preparation_suggestions(current, proposal(), SOURCE)
    assert result == current
    assert result is not current
    assert result["safetyScreening"] is not current["safetyScreening"]


def test_all_eleven_answers_can_merge_without_losing_service_association():
    existing = [payment(slot=slot) for slot in range(5)]
    additions = [provided(slot=slot) for slot in range(5)] + [{
        "questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "ANSWER_REQUEST",
    }]
    result = merge_preparation_suggestions(draft(
        situationAreas=["TREATMENT"], affectedServices=[f"Услуга {slot}" for slot in range(5)],
        analysisClarificationAnswers=answers(*existing),
    ), proposal(analysisClarificationAnswers=answers(*additions)), SOURCE)
    assert result["analysisClarificationAnswers"] == answers(*existing, *additions)


def test_legacy_global_answer_without_null_slot_is_preserved_on_equal_duplicate():
    value = {"questionId": "RESPONSE_PURPOSE", "value": "UNKNOWN"}
    current = draft(analysisClarificationAnswers=answers(value))
    assert merge_preparation_suggestions(current, proposal(
        analysisClarificationAnswers=answers(value)), SOURCE) == current


def test_screening_can_complete_existing_money_attempt_without_overwriting_it():
    source = SOURCE + " Пациент просит 10000 рублей."
    current = draft(eventSummary=source, safetyScreening={
        "schemaVersion": VERSION, "moneyRequested": "YES",
    })
    money = {"amountKopecks": 1000000, "currency": "RUB"}
    suggested = proposal(safetyScreening={
        "schemaVersion": VERSION, "moneyRequested": "YES", "amount": money,
    })
    suggested.source_spans["safetyScreening"] = [
        SourceSpan(start=len(SOURCE) + 1, end=len(source)),
    ]
    result = merge_preparation_suggestions(current, suggested, source)
    assert result["safetyScreening"] == {
        "schemaVersion": VERSION, "moneyRequested": "YES", "amount": money,
    }


def test_malformed_service_count_in_mutated_proposal_is_not_trusted():
    suggested = proposal(affectedServices=["Коронка"])
    suggested.affected_services = [f"Услуга {slot}" for slot in range(6)]
    assert_conflict(draft(), suggested)


def test_explicit_direct_identifier_in_source_is_rejected_without_retaining_context():
    assert_conflict(draft(), proposal(incomingKind="COMPLAINT"),
                    "Пациент написал test@example.com о проблеме лечения")
