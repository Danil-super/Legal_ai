"""Managers have one visible case entry: describe the situation, then missing questions."""

import pytest
from telegram_gateway.ui import main_menu_keyboard


@pytest.mark.parametrize("role", ["CLINIC_OWNER", "CLINIC_ADMIN"])
def test_main_menu_has_one_case_creation_action_leading_to_plain_description(role):
    buttons = [button for row in main_menu_keyboard(role).inline_keyboard for button in row]
    starts = [button for button in buttons if button.callback_data in {"quick:start", "case:start"}]
    assert len(starts) == 1
    assert starts[0].text == "📝 Создать кейс" and starts[0].callback_data == "quick:start"


def test_failed_legacy_extract_does_not_offer_a_second_manual_creation_flow():
    from telegram_gateway.quick_intake_runtime import _manual_keyboard

    callbacks = [button.callback_data for row in _manual_keyboard().inline_keyboard
                 for button in row]
    assert "case:start" not in callbacks and "quick:start" in callbacks
