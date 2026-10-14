"""Fixed fictional questions through the actual production Telegram composition."""

import asyncio
from copy import deepcopy

import pytest
from legal_core.factual_safety_risk import SCREENING_SIGNALS
from telegram import Update
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.guided_case_intake_v2 import active_states, next_missing_state
from test_case_experience import harness
from test_dialog_isolation import ACTOR, DRAFT
from test_factual_safety_gateway import completed_data

PAYMENT_0 = {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0}
PAYMENT_1 = {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 1}
DATE_1 = {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 1}
PURPOSE = {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None}


def requested_data(requests=None):
    return completed_data() | {
        "affectedServices": ["терапевтическое лечение", "протезирование"],
        "analysisClarificationRequests": deepcopy(
            [PAYMENT_0, PAYMENT_1, DATE_1] if requests is None else requests
        ),
    }


def seed(core, data, state="ANALYSIS_CLARIFICATION"):
    core.draft = {"id": str(DRAFT), "revision": 4, "wizardState": state,
                  "draftData": deepcopy(data)}


def answer_callback(request, value):
    slot = "global" if request["serviceSlot"] is None else request["serviceSlot"]
    return f"case:v2:clarify:{request['questionId']}:{slot}:{value}"


def buttons(sent):
    markup = sent[-1]["reply_markup"]
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def callback_on_current_message(app, callback, actor=ACTOR):
    message_id = app.user_data[ACTOR][bot.WIZARD_DATA_KEY]["_v2_prompt_message_id"]
    body = {"update_id": 10000, "callback_query": {
        "id": "forged", "chat_instance": "synthetic", "data": callback,
        "from": {"id": actor, "is_bot": False, "first_name": "Synthetic"},
        "message": {"message_id": message_id, "date": 1,
                    "chat": {"id": actor, "type": "private"}},
    }}
    await app.process_update(Update.de_json(body, app.bot))


def test_only_valid_requested_questions_extend_the_existing_sequence():
    ordinary = completed_data()
    assert "ANALYSIS_CLARIFICATION" not in active_states(ordinary)
    assert next_missing_state(ordinary) == "SUMMARY"
    requested = requested_data()
    assert active_states(requested)[-3:] == ["ANALYSIS_CLARIFICATION", "SUMMARY", "V2_CONFIRM"]
    assert next_missing_state(requested) == "ANALYSIS_CLARIFICATION"


@pytest.mark.parametrize("requests", [
    True, {"requests": [PAYMENT_0]}, [PAYMENT_0] * 2, [PAYMENT_0] * 4,
    [{"questionId": "INJECTED", "serviceSlot": 0}],
    [PAYMENT_0 | {"text": "injected"}],
    [PAYMENT_0 | {"serviceSlot": True}], [PAYMENT_0 | {"serviceSlot": 4}],
    [{"question_id": "RESPONSE_PURPOSE", "service_slot": None}],
])
def test_invalid_request_list_cannot_create_a_question(requests):
    assert "ANALYSIS_CLARIFICATION" not in active_states(requested_data(requests))


def test_composed_buttons_and_typed_date_keep_each_service_separate_and_reconfirm():
    async def scenario():
        async with harness() as (_app, core, sent, dispatch):
            source = requested_data()
            seed(core, source)
            await dispatch(f"case:draft:{DRAFT}")
            assert "Как оплачивалась услуга «терапевтическое лечение»?" in sent[-1]["text"]
            assert all(len(value.encode()) <= 64 for value in buttons(sent))
            await dispatch(answer_callback(PAYMENT_0, "PAID"))
            assert "протезирование»" in sent[-1]["text"]
            await dispatch(answer_callback(PAYMENT_1, "PUBLICLY_COVERED"))
            assert "дату события" in sent[-1]["text"]
            await dispatch("text:примерно 01.06.2020")
            assert core.draft["wizardState"] == "SUMMARY"
            answers = core.draft["draftData"]["analysisClarificationAnswers"]["answers"]
            assert answers == [PAYMENT_0 | {"value": "PAID"},
                               PAYMENT_1 | {"value": "PUBLICLY_COVERED"},
                               DATE_1 | {"value": {"date": "2020-06-01",
                                                    "precision": "APPROXIMATE"}}]
            assert core.draft["draftData"]["eventDate"] == source["eventDate"]
            assert source == requested_data()  # old case/report snapshot was not mutated
            assert core.draft["revision"] == 7
            assert "примерно 2020-06-01" in "\n".join(item.get("text", "") for item in sent)
            await dispatch("case:v2:summary:confirm")
            assert core.draft["wizardState"] == "V2_CONFIRM"
            assert f"case:confirm:{DRAFT}" in buttons(sent)
    asyncio.run(scenario())


def test_unknown_is_completed_once_and_back_reopens_only_previous_draft_answer():
    async def scenario():
        async with harness() as (_app, core, _sent, dispatch):
            seed(core, requested_data([PAYMENT_0, PURPOSE]))
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch(answer_callback(PAYMENT_0, "UNKNOWN"))
            await dispatch(answer_callback(PURPOSE, "UNKNOWN"))
            assert core.draft["wizardState"] == "SUMMARY"
            await dispatch("case:v2:back")
            assert core.draft["wizardState"] == "ANALYSIS_CLARIFICATION"
            assert core.draft["draftData"]["analysisClarificationAnswers"]["answers"] == [
                PAYMENT_0 | {"value": "UNKNOWN"},
            ]
            await dispatch(answer_callback(PURPOSE, "ANSWER_REQUEST"))
            await dispatch("case:v2:summary:confirm")
            assert core.draft["wizardState"] == "V2_CONFIRM"
            assert next_missing_state(core.draft["draftData"]) == "SUMMARY"
    asyncio.run(scenario())


def test_back_between_questions_retains_other_answers_and_cancel_restart_resumes():
    async def scenario():
        async with harness() as (app, core, _sent, dispatch):
            seed(core, requested_data())
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch(answer_callback(PAYMENT_0, "PAID"))
            await dispatch(answer_callback(PAYMENT_1, "OTHER"))
            await dispatch("case:v2:back")
            assert core.draft["draftData"]["analysisClarificationAnswers"]["answers"] == [
                PAYMENT_0 | {"value": "PAID"},
            ]
            await dispatch("/cancel")
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            persisted = deepcopy(core.draft)
        async with harness() as (_app, restarted, sent, dispatch):
            restarted.draft = persisted
            await dispatch(f"case:draft:{DRAFT}")
            assert "Как оплачивалась услуга «протезирование»?" in sent[-1]["text"]
            await dispatch(answer_callback(PAYMENT_1, "UNKNOWN"))
            await dispatch(answer_callback(DATE_1, "UNKNOWN"))
            assert restarted.draft["wizardState"] == "SUMMARY"
    asyncio.run(scenario())


def test_old_foreign_and_wrong_identity_callbacks_cannot_overwrite_answers():
    async def scenario():
        async with harness() as (app, core, sent, dispatch):
            seed(core, requested_data())
            await dispatch(f"case:draft:{DRAFT}")
            old = answer_callback(PAYMENT_0, "PAID")
            await dispatch(old)
            saved = deepcopy(core.draft)
            await dispatch(old)
            assert core.draft == saved
            assert "устарела" in sent[-1]["text"]
            await callback_on_current_message(app, old)
            assert core.draft == saved  # even a forged current message cannot remap a slot
            await callback_on_current_message(app, answer_callback(PAYMENT_1, "PAID"), ACTOR + 1)
            assert core.draft == saved
    asyncio.run(scenario())


def test_server_revision_refresh_discards_outdated_answer_then_saves_current_reply():
    async def scenario():
        async with harness() as (_app, core, sent, dispatch):
            seed(core, requested_data([PAYMENT_0]))
            await dispatch(f"case:draft:{DRAFT}")
            core.draft["revision"] += 1
            core.draft["draftData"]["clinicActionsNote"] = "Добавлено на сервере"
            await dispatch(answer_callback(PAYMENT_0, "PAID"))
            assert not core.saved
            assert "analysisClarificationAnswers" not in core.draft["draftData"]
            assert any("изменилась на сервере" in item.get("text", "") for item in sent)
            await dispatch(answer_callback(PAYMENT_0, "OTHER"))
            assert core.saved[-1]["expected_revision"] == 5
            assert core.draft["draftData"]["clinicActionsNote"] == "Добавлено на сервере"
    asyncio.run(scenario())


@pytest.mark.parametrize("edit", ["replace", "remove_treatment"])
def test_service_edits_invalidate_old_scoped_answers_and_requests_without_remapping(edit):
    async def scenario():
        async with harness() as (_app, core, _sent, dispatch):
            data = requested_data([PAYMENT_0, PURPOSE]) | {
                "analysisClarificationAnswers": {"answers": [
                    PAYMENT_0 | {"value": "PAID"}, PURPOSE | {"value": "ANSWER_REQUEST"},
                ]},
            }
            seed(core, data, "SERVICES" if edit == "replace" else "SITUATION")
            await dispatch(f"case:draft:{DRAFT}")
            if edit == "replace":
                await dispatch("text:Новая синтетическая услуга")
            else:
                await dispatch("case:v2:multi:situationAreas:TREATMENT")
                assert "affectedServices" not in core.draft["draftData"]
                assert core.draft["draftData"]["analysisClarificationRequests"] == [PURPOSE]
                await dispatch("case:v2:multi:situationAreas:DONE")
                assert "affectedServices" not in core.draft["draftData"]
            assert core.draft["draftData"]["analysisClarificationAnswers"]["answers"] == [
                PURPOSE | {"value": "ANSWER_REQUEST"},
            ]
            assert core.draft["draftData"]["analysisClarificationRequests"] == [PURPOSE]
            assert core.draft["wizardState"] == "SUMMARY"
    asyncio.run(scenario())


def test_known_urgent_confirmation_bypasses_optional_questions_without_inventing_answers():
    async def scenario():
        async with harness() as (_app, core, _sent, dispatch):
            data = requested_data([PAYMENT_0])
            data.pop("safetyScreening")
            data["healthSignals"] = ["HOSPITALIZATION"]
            seed(core, data, "SAFETY")
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("case:safety:known:confirm")
            assert core.draft["wizardState"] == "SUMMARY"
            assert "analysisClarificationAnswers" not in core.draft["draftData"]
            await dispatch("case:v2:summary:confirm")
            assert core.draft["wizardState"] == "V2_CONFIRM"
    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["quick", "guided"])
def test_actual_quick_and_manual_entry_ask_same_optional_question_only_when_requested(entry):
    async def scenario():
        async with harness() as (app, core, sent, dispatch):
            narrative = completed_data()["eventSummary"]
            if entry == "quick":
                await dispatch("quick:start")
                await dispatch("text:" + narrative)
                pending = app.user_data[ACTOR]["quick_intake_candidate"]
                await dispatch(f"quick:review:{pending['nonce']}:accept")
                await dispatch(f"case:draft:{DRAFT}")
            else:
                await dispatch("case:start")
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            await dispatch("case:v2:multi:situationAreas:TREATMENT")
            await dispatch("case:v2:multi:situationAreas:DONE")
            await dispatch("text:Терапевтическое лечение")
            if entry == "guided":
                await dispatch("text:" + narrative)
            await dispatch("text:01.09.2026")
            for callback in (
                "case:v2:single:conflictStage:ONGOING",
                "case:v2:multi:clinicActions:INVITED_FOR_EXAMINATION",
                "case:v2:multi:clinicActions:DONE",
                "case:v2:multi:healthSignals:NO_KNOWN_INFORMATION",
                "case:v2:multi:healthSignals:DONE",
                "case:v2:material:skip",
            ):
                await dispatch(callback)
            for field in SCREENING_SIGNALS:
                await dispatch(f"case:safety:{field}:NO")
            assert core.draft["wizardState"] == "SUMMARY"
            assert "analysisClarificationRequests" not in core.draft["draftData"]
            assert not any("Как оплачивалась услуга" in item.get("text", "") for item in sent)
            # Core recovery supplies only validated identities; runtime is shared for both entries.
            core.draft["draftData"]["analysisClarificationRequests"] = [PAYMENT_0, PURPOSE]
            core.draft["wizardState"] = "ANALYSIS_CLARIFICATION"
            core.draft["revision"] += 1
            await dispatch(f"case:draft:{DRAFT}")
            assert "Как оплачивалась услуга «Терапевтическое лечение»?" in sent[-1]["text"]
            await dispatch(answer_callback(PAYMENT_0, "OTHER"))
            await dispatch(answer_callback(PURPOSE, "RECEIVE_AND_EXPLAIN"))
            assert core.draft["draftData"]["analysisClarificationAnswers"]["answers"] == [
                PAYMENT_0 | {"value": "OTHER"}, PURPOSE | {"value": "RECEIVE_AND_EXPLAIN"},
            ]
            assert core.draft["draftData"]["eventSummary"] == narrative
            await dispatch("case:v2:summary:confirm")
            assert core.draft["wizardState"] == "V2_CONFIRM"
    asyncio.run(scenario())


def test_invalid_date_never_overwrites_facts_or_saves_an_attempt():
    async def scenario():
        async with harness() as (_app, core, _sent, dispatch):
            seed(core, requested_data([DATE_1]))
            await dispatch(f"case:draft:{DRAFT}")
            original = deepcopy(core.draft)
            await dispatch("text:31.02.2026")
            assert core.draft == original
            assert not core.saved
            await dispatch("text:01.06.2020")
            assert core.draft["draftData"]["analysisClarificationAnswers"]["answers"] == [
                DATE_1 | {"value": {"date": "2020-06-01", "precision": "EXACT"}},
            ]
    asyncio.run(scenario())


def test_revoked_access_prevents_cached_question_output_or_answer_save():
    async def scenario():
        async with harness() as (app, core, sent, dispatch):
            seed(core, requested_data([PAYMENT_0]))
            await dispatch(f"case:draft:{DRAFT}")
            original = deepcopy(core.draft)
            output_count = len(sent)

            async def deny(draft, actor):
                raise LegalCoreApiError(403, "ACTOR_NOT_AUTHORIZED", "synthetic refusal")

            core.get_intake_draft = deny
            await dispatch(answer_callback(PAYMENT_0, "PAID"))
            assert core.draft == original
            assert not core.saved
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            assert not any("терапевтическое лечение" in item.get("text", "")
                           for item in sent[output_count:])
    asyncio.run(scenario())
def test_core_global_request_without_optional_slot_remains_a_pending_question() -> None:
    from telegram_gateway.guided_case_intake_v2 import next_clarification_request

    request = next_clarification_request({
        "intakeVersion": 2,
        "analysisClarificationRequests": [{"questionId": "RESPONSE_PURPOSE"}],
    })
    assert request is not None
    assert request.question_id.value == "RESPONSE_PURPOSE"
    assert request.service_slot is None
