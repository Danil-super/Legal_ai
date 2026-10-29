# ruff: noqa: RUF001
"""Bounded typed questions are actionable terminal preparation cards."""

from uuid import uuid4

import pytest
from telegram_gateway.analysis_jobs_runtime import JobSnapshot, job_message
from test_preparation_job_readers import _snapshot


def test_needs_facts_names_each_typed_question_and_offers_one_answer_action():
    payload = _snapshot()
    payload["result"]["clarificationRequests"] = [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
        {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 1},
        {"questionId": "RESPONSE_PURPOSE"},
    ]
    text, markup = job_message(JobSnapshot.parse(payload))
    assert "Как оплачивалась услуга №1?" in text
    assert "Когда была оказана услуга №2?" in text
    assert "Что клинике нужно сделать в связи с обращением?" in text
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert sum(button.text == "Ответить" for button in buttons) == 1
    assert not any(button.callback_data.startswith("analysis:status:") for button in buttons)
    assert "Итоговый ответ ещё не сформирован" not in text



@pytest.mark.parametrize("status", ["NEEDS_FACTS", "LIMITED", "HUMAN_REVIEW"])
def test_terminal_preparation_result_never_offers_refresh(status):
    _text, markup = job_message(JobSnapshot.parse(_snapshot(status)))
    assert not any(button.callback_data.startswith("analysis:status:")
                   for row in markup.inline_keyboard for button in row)


@pytest.mark.parametrize("state,refresh", [("FAILED", False), ("QUEUED", True),
                                           ("RUNNING", True)])
def test_refresh_is_only_offered_while_job_can_progress(state, refresh):
    _text, markup = job_message(JobSnapshot(uuid4(), uuid4(), state, None, None))
    assert any(button.callback_data.startswith("analysis:status:")
               for row in markup.inline_keyboard for button in row) is refresh
