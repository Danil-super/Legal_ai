"""Narrative-first PTB path; only actual input becomes a confirmed case fact.

Actual PTB/selector/value contracts run; Telegram/Core HTTP are boundary fakes.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from legal_core.api_contracts import FactInput, TelegramWorkflowSubmissionRequest
from legal_core.case_api import _canonical_hash, _input_value
from legal_core.contracts import FactKey
from legal_core.intake import missing_facts_for
from legal_core.intake_preparation_contracts import DraftSuggestions
from pydantic import ValidationError
from telegram_gateway import bot
from telegram_gateway.case_wizard import facts_from_v2_data
from test_dialog_isolation import ACTOR
from test_factual_safety_gateway import completed_data
from test_initial_preparation_runtime import _context
from test_intake_single_summary import simplified_harness
from test_intake_transient_cleanup import delivery_finished


def known_data():
    data = completed_data(moneyRequested="YES", amount={"amountKopecks": 500000,
                                                       "currency": "RUB"})
    data["situationAreas"] = ["SERVICE"]
    data.pop("affectedServices")
    return data


@pytest.mark.parametrize("omitted", ["conflictStage", "clinicActions"])
def test_narrative_with_all_risk_facts_does_not_force_nonmaterial_context_before_summary(omitted):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            narrative = (
                "Учебный пример. 15.10.2026 пациент устно пожаловался на ожидание записи "
                "и попросил вернуть 5000 рублей. Ухудшения и госпитализации нет. "
                "Представители, письменные требования, документы суда или органа "
                "и угрозы обращения к ним отсутствуют. Клиника хочет получить ориентиры "
                "для ответа на обращение. "
            ) + ("Клиника пригласила на осмотр." if omitted == "conflictStage"
                 else "Это первое обращение по этой ситуации.")
            await dispatch("quick:start")
            await dispatch("text:" + narrative)
            proposed = known_data()
            for field in ("intakeVersion", "eventSummary", "incomingSourceStatus",
                          "caseMaterialsStatus", omitted):
                proposed.pop(field)
            core.propose(**proposed)
            await delivery_finished(app)
            assert omitted not in core.draft["draftData"]
            assert not core.accepts and not core.jobs
            state = app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY]
            assert state == "SUMMARY", (
                f"all risk facts supplied, but mandatory legacy field {omitted} opens {state}: "
                + "\n".join(item.get("text", "") for item in sent[-2:])
            )
    asyncio.run(scenario())


def test_mapper_and_core_accept_absent_context_without_manufacturing_statement():
    data = known_data()
    data.pop("conflictStage")
    data.pop("clinicActions")
    kept = [FactInput.model_validate(item) for item in facts_from_v2_data(data)]
    payload = TelegramWorkflowSubmissionRequest(
        intakeSchemaVersion="dental-case-intake.v2", receiptVersion=2, facts=kept,
    )
    domain = {item.fact_key: _input_value(item.value_type, item.value) for item in payload.facts}
    assert missing_facts_for(domain) == []
    assert FactKey.CONFLICT_STAGE not in domain and FactKey.CLINIC_ACTIONS not in domain


def test_complete_draft_keeps_historical_submission_hash_for_lost_response_replay():
    # Captured from the pre-change mapper on this exact synthetic draft. Core
    # binds retries to the whole canonical wire payload, including fact-list order.
    payload = TelegramWorkflowSubmissionRequest(
        intakeSchemaVersion="dental-case-intake.v2", receiptVersion=2,
        facts=facts_from_v2_data(known_data()),
    )
    assert _canonical_hash(payload.model_dump(mode="json", by_alias=True)) == (
        "628dd1c5318e7d846759073694846c9cf5b27f62618d9ba9fb5cedb7c1c77f81"
    )


def test_actual_ptb_optional_context_edits_return_to_summary_then_single_consent_submits():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:Учебная ситуация: пациент просит вернуть 5000 рублей.")
            proposed = known_data()
            for field in ("intakeVersion", "eventSummary", "incomingSourceStatus",
                          "caseMaterialsStatus", "conflictStage", "clinicActions"):
                proposed.pop(field)
            core.propose(**proposed)
            await delivery_finished(app)
            assert core.draft["wizardState"] == "SUMMARY"
            callbacks = {button.callback_data for row in sent[-1]["reply_markup"].inline_keyboard
                         for button in row}
            assert {"case:v2:context:actions", "case:v2:context:history"} <= callbacks
            assert "Что сделала клиника: не указано" in sent[-1]["text"]
            await dispatch("case:v2:context:actions")
            assert core.draft["wizardState"] == "CLINIC_ACTIONS"
            await dispatch("/menu")
            await dispatch("case:draft:" + core.draft["id"])
            assert core.draft["wizardState"] == "CLINIC_ACTIONS"
            await dispatch("case:v2:multi:clinicActions:OFFERED_REFUND")
            assert core.draft["wizardState"] == "CLINIC_ACTIONS"
            await dispatch("case:v2:multi:clinicActions:DONE")
            assert core.draft["wizardState"] == "SUMMARY"
            await dispatch("/menu")
            await dispatch("case:draft:" + core.draft["id"])
            assert core.draft["wizardState"] == "SUMMARY"
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SUMMARY"
            await dispatch("case:v2:context:history")
            assert core.draft["wizardState"] == "CHRONOLOGY"
            await dispatch("case:v2:single:conflictStage:ONGOING")
            assert core.draft["wizardState"] == "SUMMARY"
            assert "Ход ситуации: ситуация продолжается" in sent[-1]["text"]
            assert not core.accepts and not core.jobs
            receipt = _context()[2].submit_workflow.return_value
            core.submit_workflow = AsyncMock(return_value=receipt)
            core.archive_intake_draft = AsyncMock()
            with patch("telegram_gateway.dialogue_submission.enqueue_analysis",
                       new_callable=AsyncMock) as enqueue:
                await dispatch("case:v2:summary:confirm")
            core.submit_workflow.assert_awaited_once()
            enqueue.assert_awaited_once()
            submitted = core.submit_workflow.await_args.args[1]
            by_key = {item["factKey"]: item["value"] for item in submitted}
            assert by_key["CONFLICT_STAGE"] == {"value": "ONGOING"}
            assert by_key["CLINIC_ACTIONS"] == {"values": ["OFFERED_REFUND"]}
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
    asyncio.run(scenario())


def test_unknown_is_not_a_general_absent_field_or_inference_transport_escape_hatch():
    stage = DraftSuggestions.model_validate({"conflictStage": "UNKNOWN", "sourceSpans": {
        "conflictStage": [{"start": 0, "end": 7}],
    }})
    assert stage.conflict_stage == "UNKNOWN"
    with pytest.raises(ValidationError):
        DraftSuggestions.model_validate({"clinicActions": ["UNKNOWN"], "sourceSpans": {
            "clinicActions": [{"start": 0, "end": 7}],
        }})
    wire = facts_from_v2_data(known_data())[0] | {"sourceType": "INFERENCE"}
    with pytest.raises(ValidationError):
        FactInput.model_validate(wire)


@pytest.mark.parametrize("editor", ["actions", "history", "actions-selected", "actions-cleared"])
def test_optional_summary_editor_back_returns_to_summary_without_another_form_step(editor):
    async def scenario():
        async with simplified_harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:Учебная ситуация: пациент просит вернуть 5000 рублей.")
            proposed = known_data()
            for field in ("intakeVersion", "eventSummary", "incomingSourceStatus",
                          "caseMaterialsStatus", "conflictStage", "clinicActions"):
                proposed.pop(field)
            core.propose(**proposed)
            await delivery_finished(app)
            assert core.draft["wizardState"] == "SUMMARY"
            target = "actions" if editor.startswith("actions") else "history"
            await dispatch("case:v2:context:" + target)
            expected = "CLINIC_ACTIONS" if target == "actions" else "CHRONOLOGY"
            assert core.draft["wizardState"] == expected
            if editor in {"actions-selected", "actions-cleared"}:
                await dispatch("case:v2:multi:clinicActions:OFFERED_REFUND")
                assert core.draft["wizardState"] == "CLINIC_ACTIONS"
                if editor == "actions-cleared":
                    await dispatch("case:v2:multi:clinicActions:OFFERED_REFUND")
            await dispatch("case:v2:back")
            assert core.draft["wizardState"] == "SUMMARY"
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SUMMARY"
            if editor == "actions-selected":
                assert core.draft["draftData"]["clinicActions"] == ["OFFERED_REFUND"]
            else:
                assert "clinicActions" not in core.draft["draftData"]
            assert not core.accepts and not core.jobs
    asyncio.run(scenario())
