"""Durable callback identity is independent of process-local conversation state."""

from uuid import UUID

import pytest
from telegram_gateway.understanding_intent import (
    bound_callback,
    parse_bound_callback,
    source_keys,
)


def test_callbacks_are_canonical_bounded_and_round_trip_without_ram():
    operation = UUID("00000000-0000-4000-8000-000000000009")
    draft_id = UUID("00000000-0000-4000-8000-000000000019")
    for action in ("refresh", "accept", "manual", "cancel", "edit"):
        encoded = bound_callback(action, operation, draft_id)
        assert len(encoded.encode()) <= 64
        assert parse_bound_callback(encoded) == (action, operation, draft_id)


@pytest.mark.parametrize("value", [
    "understanding:a1:not-a-uuid", "understanding:a1:" + "A" * 32,
    "understanding:a1:" + "0" * 43 + ":extra", "understanding:drop1:" + "0" * 43,
])
def test_malformed_callbacks_have_no_fallback_to_latest_operation(value):
    with pytest.raises(ValueError):
        parse_bound_callback(value)


def test_source_keys_are_stable_purpose_separated_and_event_scoped():
    first = source_keys(42, 42, 7)
    assert first == source_keys(42, 42, 7)
    assert first[0] != first[1]
    assert len(set(first + source_keys(42, 42, 8) + source_keys(43, 43, 7))) == 6


@pytest.mark.parametrize("identity", [(True, 42, 7), (42, 42, 0), (42, None, 7)])
def test_missing_original_event_identity_never_generates_random_retry_keys(identity):
    with pytest.raises(ValueError):
        source_keys(*identity)


def test_bound_callbacks_recover_pending_draft_and_operation_without_ram():
    operation = UUID("00000000-0000-4000-8000-000000000009")
    draft_id = UUID("00000000-0000-4000-8000-000000000019")
    for action in ("refresh", "accept", "manual", "cancel", "edit"):
        value = bound_callback(action, operation, draft_id)
        assert len(value.encode()) <= 64
        assert parse_bound_callback(value) == (action, operation, draft_id)
        with pytest.raises(ValueError):
            parse_bound_callback(value + "=")
