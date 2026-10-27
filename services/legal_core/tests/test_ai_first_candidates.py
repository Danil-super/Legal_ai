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
