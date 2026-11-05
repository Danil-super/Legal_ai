"""Construct both production handler configurations offline inside the built image."""

import os
from datetime import datetime

from telegram.ext import CommandHandler

from telegram_gateway.case_experience_runtime import build_application_with_case_experience
from telegram_gateway.case_history_runtime import _date
from telegram_gateway.completed_report import ready_report


def main() -> None:
    # This standalone build/CI process never reads production credentials or starts polling.
    os.environ.update({
        "AGENT_ORCHESTRATOR_URL": "http://orchestrator.invalid",
        "AGENT_INTERNAL_KEY": "synthetic-offline-internal-" * 3,
        "LEGAL_EDITOR_GATEWAY_KEY": "synthetic-offline-editor-" * 3,
    })
    for enabled in (False, True):
        app = build_application_with_case_experience(
            "123456:" + "synthetic_offline_token_" * 2,
            enable_understanding=enabled, enable_answer_interpretation=enabled,
            enable_simplified_dialogue=enabled, enable_lawyer_work_products=enabled,
        )
        assert not app.running
        assert any(isinstance(handler, CommandHandler) and "cases" in handler.commands
                   for group in app.handlers.values() for handler in group)
        assert app.bot_data["intake_simplified_dialogue"] is enabled
    assert callable(ready_report)
    assert _date(datetime.fromisoformat("2026-11-05T22:30:00+00:00")) == "06.11.2026"
    print("Offline production application construction passed")


if __name__ == "__main__":
    main()
