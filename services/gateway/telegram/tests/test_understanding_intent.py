"""Durable callback identity is independent of process-local conversation state."""

from uuid import UUID

import pytest
from telegram_gateway.understanding_intent import callback, parse_callback, source_keys


def test_callbacks_are_canonical_bounded_and_round_trip_without_ram():
    operation = UUID("00000000-0000-4000-8000-000000000009")
    for action in ("refresh", "accept", "manual", "cancel", "edit"):
        encoded = callback(action, operation)
        assert len(encoded.encode()) <= 64
        assert parse_callback(encoded) == (action, operation)


@pytest.mark.parametrize("value", [
    "understanding:accept1:not-a-uuid", "understanding:accept1:" + "A" * 32,
    "understanding:accept1:" + "0" * 32 + ":extra", "understanding:drop1:" + "0" * 32,
])
def test_malformed_callbacks_have_no_fallback_to_latest_operation(value):
    with pytest.raises(ValueError):
        parse_callback(value)


def test_source_keys_are_stable_purpose_separated_and_event_scoped():
    first = source_keys(42, 42, 7)
    assert first == source_keys(42, 42, 7)
    assert first[0] != first[1]
    assert len(set(first + source_keys(42, 42, 8) + source_keys(43, 43, 7))) == 6


@pytest.mark.parametrize("identity", [(True, 42, 7), (42, 42, 0), (42, None, 7)])
def test_missing_original_event_identity_never_generates_random_retry_keys(identity):
    with pytest.raises(ValueError):
        source_keys(*identity)
