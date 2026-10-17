"""Restart-safe content-free answer selectors never alias initial-understanding actions."""

from uuid import UUID

import pytest
from telegram_gateway.answer_intent import bound_callback, parse_bound_callback
from telegram_gateway.understanding_intent import bound_callback as initial_callback

OP = UUID("00000000-0000-4000-8000-000000000051")
DRAFT = UUID("00000000-0000-4000-8000-000000000052")


@pytest.mark.parametrize("action", ["refresh", "accept", "manual", "cancel", "edit", "menu"])
def test_compact_answer_selectors_round_trip_without_process_memory(action):
    value = bound_callback(action, OP, DRAFT)
    assert len(value.encode()) <= 64
    assert parse_bound_callback(value) == (action, OP, DRAFT)
    assert value != initial_callback(action, OP, DRAFT)


@pytest.mark.parametrize("value", ["answer:a1:bad", "answer:x1:" + "A" * 43,
                                  "answer:a1:" + "A" * 43 + "=",
                                  initial_callback("accept", OP, DRAFT)])
def test_invalid_or_other_flow_selectors_never_fallback_to_latest(value):
    with pytest.raises(ValueError):
        parse_bound_callback(value)
