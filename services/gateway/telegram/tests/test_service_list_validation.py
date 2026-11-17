"""Malformed explicitly formatted service lists receive a precise, retryable validation error."""

import asyncio

import pytest
from test_answer_runtime import answer_harness
from test_dialog_isolation import DRAFT


@pytest.mark.parametrize("source,detail", [
    ("осмотр; лечение; гигиена; рентген; коронка; винир", "от одной до пяти"),
    ("осмотр; осмотр", "от одной до пяти"),
    ("осмотр; " + "x" * 121, "от 2 до 120"),
    ("осмотр; +7 999 123-45-67", "персональные данные"),
])
def test_invalid_formatted_list_does_not_start_a_model_or_overwrite_the_draft(source, detail):
    async def scenario():
        async with answer_harness() as (_app, core, sent, dispatch):
            core.draft.update(wizardState="SERVICES", draftData={
                "intakeVersion": 2, "incomingKind": "COMPLAINT",
                "incomingSourceStatus": "NOT_ATTACHED", "situationAreas": ["TREATMENT"],
                "eventSummary": "Синтетический спор: оплатили медицинскую услугу.",
            })
            await dispatch(f"case:draft:{DRAFT}")
            before = core.draft.copy()
            await dispatch("text:" + source)
            assert not core.starts and not core.saved
            assert core.draft == before
            assert detail in sent[-1]["text"]
            await dispatch("text:Осмотр; лечение кариеса")
            assert len(core.starts) == 1
            assert core.draft == before
            assert "Ответ пока не подтверждён" not in sent[-1]["text"]
    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["PENDING", "PROPOSED"])
def test_literal_service_retry_does_not_replace_an_inflight_or_ready_answer(state):
    async def scenario():
        async with answer_harness() as (_app, core, _sent, dispatch):
            core.draft.update(wizardState="SERVICES", draftData={
                "intakeVersion": 2, "incomingKind": "COMPLAINT",
                "incomingSourceStatus": "NOT_ATTACHED", "situationAreas": ["TREATMENT"],
                "eventSummary": "Синтетический спор: оплатили медицинскую услугу.",
            })
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Предыдущее описание названий услуг")
            operation = core.starts[-1]
            if state == "PROPOSED":
                core.propose(["Предыдущее описание названий услуг"])
            await dispatch("text:Осмотр; лечение кариеса")
            assert core.starts == [operation] and core.cancels == []
            assert "affectedServices" not in core.draft["draftData"]
    asyncio.run(scenario())
