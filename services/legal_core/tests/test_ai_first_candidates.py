"""Rich factual proposals cannot replace other attempts or escape their source scope."""

import hashlib
from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest

from legal_core.intake_answer_contracts import (
    AnswerInterpretation,
    FactualContextProposal,
    RetainedSourceSegment,
    derive_answer_question,
)
from legal_core.intake_factual_candidates import (
    allowed_factual_questions,
    apply_factual_answer,
    retained_answer_sources,
    validate_factual_proposal,
)

AS_OF = date(2026, 10, 27)
DATA = {"intakeVersion": 2, "situationAreas": ["SERVICE"],
        "eventSummary": "Пациент обратился 15.10.2026 по поводу обслуживания."}
ANSWER = "Пригласили на осмотр. Пациент просит вернуть 5000 рублей."


def context(data=None):
    data = deepcopy(DATA if data is None else data)
    current = derive_answer_question(data, "CLINIC_ACTIONS")
    sources = retained_answer_sources(uuid4(), 3, data, ANSWER)
    allowed = allowed_factual_questions(data, current)
    return data, current, sources, allowed


def candidate(question, source, value, text):
    start = source.text.index(text)
    return {"question": question.model_dump(mode="json", by_alias=True), "value": value,
            "anchors": [{"sourceId": str(source.source_id),
                         "sourceSha256": source.source_sha256,
                         "start": start, "end": start + len(text)}]}


def rich(data=None):
    data, current, sources, allowed = context(data)
    event = next(item for item in allowed if item.target_field == "eventDate")
    proposal = FactualContextProposal.model_validate({
        "schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
            candidate(event, sources[0], {"date": "2026-10-15", "precision": "EXACT"},
                      "15.10.2026"),
        ],
    })
    primary = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": ["INVITED_FOR_EXAMINATION"],
        "sourceSpans": [{"start": 0, "end": 19}],
        "factualProposal": proposal.model_dump(mode="json", by_alias=True),
    })
    return data, current, sources, allowed, proposal, primary


def test_rich_answer_retains_additional_date_from_original_without_replacing_narrative():
    data, current, sources, allowed, proposal, primary = rich()
    original = deepcopy(data)
    validate_factual_proposal(proposal, sources, allowed, data, as_of_date=AS_OF)
    result = apply_factual_answer(data, current, primary, ANSWER, sources, as_of_date=AS_OF)
    assert result["eventDate"] == {"date": "2026-10-15", "precision": "EXACT"}
    assert result["clinicActions"] == ["INVITED_FOR_EXAMINATION"]
    assert result["eventSummary"] == original["eventSummary"]
    assert data == original and "confirmed" not in result


def test_retained_sources_are_deterministic_current_and_contain_no_uploads():
    identity = uuid4()
    data = DATA | {"materialsText": "SYNTHETIC_FILE_PRIVATE", "clinicId": str(uuid4())}
    sources = retained_answer_sources(identity, 3, data, ANSWER)
    assert sources == retained_answer_sources(identity, 3, data, ANSWER)
    assert len(sources) == 2 and {item.revision for item in sources} == {3}
    assert sources[0].kind == "INITIAL_DESCRIPTION" and sources[1].kind == "FOLLOWUP_ANSWER"
    assert all(item.source_sha256 == hashlib.sha256(item.text.encode()).hexdigest()
               for item in sources)
    assert "SYNTHETIC_FILE_PRIVATE" not in str(sources)
    assert sources != retained_answer_sources(identity, 4, data, ANSWER)


def test_allowed_additions_preserve_existing_attempts_and_do_not_offer_narrative_rewrites():
    _, current, _, allowed = context(DATA | {
        "eventDate": {"date": None, "precision": "UNKNOWN"},
        "safetyScreening": {"schemaVersion": "factual-safety-intake.v1",
                            "hospitalizationReported": "NO"},
    })
    assert all(item.target_field not in {"eventDate", "eventSummary"} for item in allowed)
    assert all(item.screening_field != "hospitalizationReported" for item in allowed)
    assert all(item.question_sha256 != current.question_sha256 for item in allowed)


def test_proposal_validator_independently_rejects_targets_not_in_the_server_list():
    data, _, sources, _, proposal, _ = rich()
    with pytest.raises(ValueError):
        validate_factual_proposal(proposal, sources, [], data, as_of_date=AS_OF)


@pytest.mark.parametrize("fault", ["known-target", "primary-target", "false-value",
                                   "future-date", "foreign-source", "scope"])
def test_overreaching_or_ungrounded_additions_do_not_partially_apply(fault):
    data, current, sources, _, proposal, primary = rich()
    wire = proposal.model_dump(mode="json", by_alias=True)
    item = wire["candidates"][0]
    if fault == "known-target":
        data["eventDate"] = {"date": None, "precision": "UNKNOWN"}
    elif fault == "primary-target":
        item["question"] = current.model_dump(mode="json", by_alias=True)
        item["value"] = ["INVITED_FOR_EXAMINATION"]
    elif fault == "false-value":
        item["value"] = {"date": "NOT_A_DATE", "precision": "EXACT"}
    elif fault == "future-date":
        item["value"] = {"date": "2027-10-15", "precision": "EXACT"}
    elif fault == "foreign-source":
        item["anchors"][0]["sourceId"] = str(uuid4())
    else:
        item["question"] = derive_answer_question(
            {"intakeVersion": 2, "situationAreas": ["TREATMENT"],
             "affectedServices": ["Другая синтетическая услуга"]}, "SERVICES",
        ).model_dump(mode="json", by_alias=True)
        item["value"] = ["Другая синтетическая услуга"]
    bad = FactualContextProposal.model_validate(wire)
    original = deepcopy(data)
    changed = AnswerInterpretation.model_validate({
        **primary.model_dump(by_alias=True), "factualProposal": bad,
    })
    with pytest.raises(ValueError):
        apply_factual_answer(data, current, changed, ANSWER, sources, as_of_date=AS_OF)
    assert data == original


def test_source_revision_and_current_answer_binding_are_required():
    data, current, sources, _, _, primary = rich()
    changed = RetainedSourceSegment.model_validate({
        **sources[-1].model_dump(by_alias=True), "revision": 2,
    })
    with pytest.raises(ValueError):
        apply_factual_answer(data, current, primary, ANSWER, [sources[0], changed],
                             as_of_date=AS_OF)


def test_legacy_single_answer_remains_exactly_compatible_without_retained_sources():
    data, current, _, _ = context()
    primary = AnswerInterpretation(status="PROPOSED", value=["INVITED_FOR_EXAMINATION"],
                                   sourceSpans=[{"start": 0, "end": 19}])
    assert "factualProposal" not in primary.model_dump(mode="json", by_alias=True)
    assert apply_factual_answer(data, current, primary, ANSWER, [], as_of_date=AS_OF) == (
        DATA | {"clinicActions": ["INVITED_FOR_EXAMINATION"]}
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_rich_reply_keeps_exact_money_and_signal_atomically_regardless_of_candidate_order(reverse):
    data, current, sources, allowed, _, primary = rich()
    money = next(item for item in allowed if item.screening_field == "moneyRequested")
    amount = next(item for item in allowed if item.screening_field == "amount")
    additions = [candidate(money, sources[-1], "YES", "просит вернуть 5000 рублей"),
                 candidate(amount, sources[-1], {"amountKopecks": 500000, "currency": "RUB"},
                           "5000 рублей")]
    proposal = FactualContextProposal.model_validate({
        "schemaVersion": "ai-first-factual-proposal.v1",
        "candidates": additions[::-1] if reverse else additions,
    })
    interpretation = AnswerInterpretation.model_validate({
        **primary.model_dump(by_alias=True), "factualProposal": proposal,
    })
    result = apply_factual_answer(data, current, interpretation, ANSWER, sources, as_of_date=AS_OF)
    assert result["safetyScreening"] == {
        "schemaVersion": "factual-safety-intake.v1", "moneyRequested": "YES",
        "amount": {"amountKopecks": 500000, "currency": "RUB"},
    }


def test_number_in_original_text_cannot_become_an_amount_without_currency():
    data, current, sources, allowed, _, primary = rich()
    amount = next(item for item in allowed if item.screening_field == "amount")
    raw = {"schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
        candidate(amount, sources[-1], {"amountKopecks": 500000, "currency": "RUB"}, "5000"),
    ]}
    interpretation = AnswerInterpretation.model_validate({
        **primary.model_dump(by_alias=True), "factualProposal": raw,
    })
    with pytest.raises(ValueError):
        apply_factual_answer(data, current, interpretation, ANSWER, sources, as_of_date=AS_OF)


def test_one_service_reply_retains_payment_and_its_own_service_date():
    data = {**DATA, "situationAreas": ["TREATMENT"], "affectedServices": ["Установка коронки"],
            "analysisClarificationRequests": [
                {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
                {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0},
            ]}
    current = derive_answer_question(data, "ANALYSIS_CLARIFICATION")
    allowed = allowed_factual_questions(data, current)
    extra = next(item for item in allowed if item.clarification_request is not None)
    answer = "Услуга платная, проведена 15.10.2026."
    sources = retained_answer_sources(uuid4(), 3, data, answer)
    interpreted = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": "PAID", "sourceSpans": [{"start": 0, "end": 14}],
        "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
            candidate(extra, sources[-1], {"date": "2026-10-15", "precision": "EXACT"},
                      "15.10.2026"),
        ]},
    })
    result = apply_factual_answer(data, current, interpreted, answer, sources, as_of_date=AS_OF)
    assert result["analysisClarificationAnswers"] == {"answers": [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "PAID"},
        {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
         "value": {"date": "2026-10-15", "precision": "EXACT"}},
    ]}


def test_replacing_services_cannot_apply_an_additional_date_bound_to_old_slots():
    data = {**DATA, "situationAreas": ["TREATMENT"], "affectedServices": ["Прежняя услуга"],
            "analysisClarificationRequests": [
                {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0},
            ]}
    current = derive_answer_question(data, "SERVICES")
    extra = derive_answer_question(data, "ANALYSIS_CLARIFICATION")
    answer = "Новая услуга, проведена 15.10.2026."
    sources = retained_answer_sources(uuid4(), 3, data, answer)
    interpreted = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": ["Новая услуга"],
        "sourceSpans": [{"start": 0, "end": 12}],
        "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
            candidate(extra, sources[-1], {"date": "2026-10-15", "precision": "EXACT"},
                      "15.10.2026"),
        ]},
    })
    before = deepcopy(data)
    with pytest.raises(ValueError):
        apply_factual_answer(data, current, interpreted, answer, sources, as_of_date=AS_OF)
    assert data == before


@pytest.mark.parametrize("state,field,source,value", [
    ("INCOMING", "incomingKind", "Это устное обращение", "MESSAGE_OR_REQUEST"),
    ("HEALTH", "healthSignals", "Сведений о последствиях нет", ["NO_KNOWN_INFORMATION"]),
])
def test_first_fill_does_not_erase_previously_extracted_independent_safety_answers(
    state, field, source, value,
):
    from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION

    screening = {"schemaVersion": SCREENING_VERSION, **dict.fromkeys(SCREENING_FIELDS, "NO"),
                 "moneyRequested": "YES", "amount": {"amountKopecks": 500000, "currency": "RUB"}}
    data = {**DATA, "safetyScreening": screening}
    current = derive_answer_question(data, state)
    interpreted = AnswerInterpretation(status="PROPOSED", value=value,
        sourceSpans=[{"start": 0, "end": len(source)}])
    result = apply_factual_answer(data, current, interpreted, source, [], as_of_date=AS_OF)
    assert result[field] == value
    assert result["safetyScreening"] == screening


def test_actual_replacement_keeps_existing_dependency_invalidation():
    data = {**DATA, "incomingKind": "MESSAGE_OR_REQUEST", "safetyScreening": {
        "schemaVersion": "factual-safety-intake.v1", "writtenRequirementsReceived": "NO",
    }}
    source = "Теперь это письменная претензия"
    current = derive_answer_question(data, "INCOMING")
    interpreted = AnswerInterpretation(status="PROPOSED", value="FORMAL_DOCUMENT",
        sourceSpans=[{"start": 0, "end": len(source)}])
    result = apply_factual_answer(data, current, interpreted, source, [], as_of_date=AS_OF)
    assert "safetyScreening" not in result


def test_new_service_reply_preserves_its_payment_and_date_before_analysis_requests_exist():
    data = {**DATA, "situationAreas": ["TREATMENT"]}
    current = derive_answer_question(data, "SERVICES")
    allowed = allowed_factual_questions(data, current)
    date_question = next(item for item in allowed if item.clarification_request is not None
                         and item.clarification_request.question_id == "SERVICE_PROVIDED_DATE"
                         and item.clarification_request.service_slot == 0)
    payment = next(item for item in allowed if item.clarification_request is not None
                   and item.clarification_request.question_id == "SERVICE_PAYMENT_BASIS"
                   and item.clarification_request.service_slot == 0)
    answer = "Установка коронки выполнена 15.10.2026, услуга платная."
    sources = retained_answer_sources(uuid4(), 3, data, answer)
    interpreted = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": ["Установка коронки"],
        "sourceSpans": [{"start": 0, "end": len("Установка коронки")}],
        "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
            candidate(date_question, sources[-1], {"date": "2026-10-15", "precision": "EXACT"},
                      answer), candidate(payment, sources[-1], "PAID", answer),
        ]},
    })
    result = apply_factual_answer(data, current, interpreted, answer, sources, as_of_date=AS_OF)
    assert result["affectedServices"] == ["Установка коронки"]
    assert result["analysisClarificationAnswers"] == {"answers": [
        {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
         "value": {"date": "2026-10-15", "precision": "EXACT"}},
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "PAID"},
    ]}


@pytest.mark.parametrize("fault", ["out-of-bounds", "no-service-label", "existing-unknown"])
def test_new_service_templates_cannot_escape_slots_or_replace_existing_attempts(fault):
    data = {**DATA, "situationAreas": ["TREATMENT"]}
    if fault == "existing-unknown":
        data["affectedServices"] = ["Установка коронки"]
        data["analysisClarificationAnswers"] = {"answers": [{
            "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
            "value": {"date": None, "precision": "UNKNOWN"},
        }]}
    current = derive_answer_question(data, "SERVICES")
    slot = 4 if fault == "out-of-bounds" else 0
    extra = next(item for item in allowed_factual_questions(data, current)
                 if item.scope_basis == "PROPOSED_SERVICES"
                 and item.clarification_request.question_id == "SERVICE_PROVIDED_DATE"
                 and item.clarification_request.service_slot == slot)
    answer = "Установка коронки выполнена 15.10.2026."
    sources = retained_answer_sources(uuid4(), 3, data, answer)
    value = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": ["Установка коронки"],
        "sourceSpans": [{"start": 0, "end": len("Установка коронки")}],
        "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1", "candidates": [
            candidate(extra, sources[-1], {"date": "2026-10-15", "precision": "EXACT"},
                      "15.10.2026" if fault == "no-service-label" else answer),
        ]},
    })
    before = deepcopy(data)
    with pytest.raises(ValueError):
        apply_factual_answer(data, current, value, answer, sources, as_of_date=AS_OF)
    assert data == before


def test_rich_five_service_reply_can_retain_all_explicit_supported_fields_in_one_batch():
    services = ["Установка коронки", "Пломба", "Имплантация", "Удаление зуба", "Лечение каналов"]
    clauses = [f"{label} выполнена 10.10.2026, платно." for label in services]
    answer = " ".join(clauses) + (
        " Первое устное обращение поступило 15.10.2026. Клиника пока ничего не сделала."
        " Ухудшения и госпитализации не было, представителей нет, письменных требований,"
        " документов суда и угроз обращения к нему нет. Пациент просит вернуть 5000 рублей."
    )
    data = {**DATA, "situationAreas": ["TREATMENT"]}
    current = derive_answer_question(data, "SERVICES")
    sources = retained_answer_sources(uuid4(), 3, data, answer)
    additions = []
    values = {"incomingKind": "MESSAGE_OR_REQUEST", "conflictStage": "FIRST",
              "clinicActions": ["NOTHING_YET"], "healthSignals": ["NO_KNOWN_INFORMATION"],
              "eventDate": {"date": "2026-10-15", "precision": "EXACT"}}
    for question in allowed_factual_questions(data, current):
        literal = answer
        if question.scope_basis is not None:
            request = question.clarification_request
            literal = clauses[request.service_slot]
            value = "PAID" if request.question_id == "SERVICE_PAYMENT_BASIS" else {
                "date": "2026-10-10", "precision": "EXACT",
            }
        elif question.screening_field is not None:
            value = "NO"
            if question.screening_field == "moneyRequested":
                value = "YES"
            elif question.screening_field == "amount":
                value, literal = {"amountKopecks": 500000, "currency": "RUB"}, "5000 рублей"
        else:
            value = values[question.target_field]
        additions.append(candidate(question, sources[-1], value, literal))
    assert len(additions) > 12
    interpreted = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": services,
        "sourceSpans": [{"start": 0, "end": len(answer)}],
        "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1",
                            "candidates": additions},
    })
    result = apply_factual_answer(data, current, interpreted, answer, sources, as_of_date=AS_OF)
    assert result["safetyScreening"]["amount"] == {"amountKopecks": 500000, "currency": "RUB"}
    assert len(result["analysisClarificationAnswers"]["answers"]) == 10
    assert result["eventDate"] == {"date": "2026-10-15", "precision": "EXACT"}
