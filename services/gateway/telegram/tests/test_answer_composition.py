"""Full intake composition is explicit, local to one app, and default-off."""

import pytest
from telegram_gateway.answer_runtime import route_input
from telegram_gateway.case_experience_runtime import build_application_with_case_experience


@pytest.mark.parametrize("full", [False, True])
def test_full_answer_consumer_is_an_explicit_composition_opt_in(full):
    app = build_application_with_case_experience(
        "123456:" + "q" * 40, enable_understanding=True, enable_answer_interpretation=full,
    )
    assert any(getattr(handler, "callback", None) is route_input
               for group in app.handlers.values() for handler in group) is full


def test_answer_consumer_requires_shared_pre_report_preparation():
    with pytest.raises(ValueError, match="shared preparation"):
        build_application_with_case_experience(
            "123456:" + "q" * 40, enable_answer_interpretation=True,
        )
