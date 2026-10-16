"""Production entry enables the complete clinical flow atomically or leaves it off."""

from unittest.mock import Mock

import pytest
from telegram.ext import Application
from telegram_gateway import bot
from telegram_gateway import case_experience_runtime as runtime
from telegram_gateway.answer_runtime import route_input


@pytest.mark.parametrize("value,enabled", [(None, False), ("0", False), ("1", True)])
def test_main_passes_one_validated_flag_to_both_clinical_consumers(value, enabled, monkeypatch):
    if value is None:
        monkeypatch.delenv("PRE_REPORT_INTAKE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PRE_REPORT_INTAKE_ENABLED", value)
    app = Mock()
    factory = Mock(return_value=app)
    monkeypatch.setattr(runtime, "build_application_with_case_experience", factory)
    monkeypatch.setattr(runtime.gateway_bot, "load_token", lambda: "synthetic-token")
    runtime.main()
    factory.assert_called_once_with("synthetic-token", enable_understanding=enabled,
                                    enable_answer_interpretation=enabled)
    app.run_polling.assert_called_once_with(allowed_updates=bot.ALLOWED_UPDATES,
                                           bootstrap_retries=3, drop_pending_updates=False)


@pytest.mark.parametrize("value", ["", "true", "False", "yes", "on", " 1", "1 ", "2", "01",
                                  "SYNTHETIC_PRIVATE_CONFIG"])
def test_invalid_flag_fails_before_token_read_application_construction_or_polling(
    value, monkeypatch,
):
    monkeypatch.setenv("PRE_REPORT_INTAKE_ENABLED", value)
    factory, token = Mock(), Mock()
    monkeypatch.setattr(runtime, "build_application_with_case_experience", factory)
    monkeypatch.setattr(runtime.gateway_bot, "load_token", token)
    with pytest.raises(RuntimeError) as caught:
        runtime.main()
    assert str(caught.value) == "PRE_REPORT_INTAKE_ENABLED must be exactly 0 or 1"
    factory.assert_not_called()
    token.assert_not_called()


@pytest.mark.parametrize("value,enabled", [("0", False), ("1", True)])
def test_real_main_composition_remains_clinical_even_with_personal_preview_requested(
    value, enabled, monkeypatch,
):
    monkeypatch.setenv("PRE_REPORT_INTAKE_ENABLED", value)
    monkeypatch.setenv("PERSONAL_PREVIEW_MODE", "synthetic")
    monkeypatch.delenv("PERSONAL_PREVIEW_API_KEY", raising=False)
    monkeypatch.setattr(runtime.gateway_bot, "load_token", lambda: "123456:" + "q" * 40)
    factory, applications = runtime.build_application_with_case_experience, []

    def capture(*args, **kwargs):
        app = factory(*args, **kwargs)
        applications.append(app)
        return app

    poll = Mock()
    monkeypatch.setattr(runtime, "build_application_with_case_experience", capture)
    monkeypatch.setattr(Application, "run_polling", poll)
    runtime.main()
    app = applications[0]
    assert any(getattr(handler, "callback", None) is route_input
               for group in app.handlers.values() for handler in group) is enabled
    pending = [handler for group in app.handlers.values() for handler in group]
    while pending:
        handler = pending.pop()
        callback = getattr(handler, "callback", None)
        if callback is not None:
            assert not callback.__module__.startswith("telegram_gateway.personal")
        pending.extend(getattr(handler, "entry_points", ()))
        pending.extend(getattr(handler, "fallbacks", ()))
        for children in getattr(handler, "states", {}).values():
            pending.extend(children)
    poll.assert_called_once_with(allowed_updates=bot.ALLOWED_UPDATES,
                                 bootstrap_retries=3, drop_pending_updates=False)
