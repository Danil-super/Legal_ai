# ruff: noqa: RUF001
"""Deadline regressions through the actual composed gateway, without external I/O."""

import asyncio
from copy import deepcopy
from itertools import product

import pytest
from legal_core.api_contracts import TelegramWorkflowSubmissionRequest
from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import CaseDraft, facts_from_draft
from telegram_gateway.intake_experience import next_missing_state, parse_answer, valid_value
from test_case_experience import complete_data, harness
from test_dialog_isolation import ACTOR, DRAFT

SOURCES = (
    ("formal_claim", "CLAIM_DEADLINE"),
    ("lawyer_contact", "LAWYER_DEADLINE"),
    ("regulator_or_court", "AUTHORITY_DEADLINE"),
)
COMBINATIONS = list(product((False, True), repeat=3))


def data_with_sources(flags):
    data = complete_data()
    for (field, _state), present in zip(SOURCES, flags, strict=True):
        data[field] = "YES" if present else "NO"
    if flags[0]:
        data["claim_received_at"] = {"date": "2026-07-02", "precision": "EXACT"}
    if flags[1]:
        data["representative_authority"] = "UNKNOWN"
    if flags[2]:
        data["authority_kind"] = "Суд"
        data["authority_document_date"] = {"date": "2026-07-03", "precision": "EXACT"}
    return data


def assert_wire_deadline(data, expected):
    facts = facts_from_draft(CaseDraft(**data))
    # Also validates the original server contract and uniqueness of every fact key.
    request = TelegramWorkflowSubmissionRequest.model_validate({
        "intakeSchemaVersion": "dental-case-intake.v1", "locale": "ru-RU", "facts": facts,
    })
    assert len(request.facts) == len(facts)
    deadlines = [fact for fact in facts if fact["factKey"] == "RESPONSE_DEADLINE"]
    if expected is None:
        assert deadlines == []
    else:
        assert len(deadlines) == 1
        assert deadlines[0]["value"] == expected
        assert deadlines[0]["sourceType"] == "USER_STATEMENT"


@pytest.mark.parametrize("flags", COMBINATIONS)
@pytest.mark.parametrize("answers", [
    ("30.09.2026", "28.09.2026", "25.09.2026"),
    ("неизвестно", "неизвестно", "неизвестно"),
    ("неизвестно", "28.09.2026", "25.09.2026"),
    ("25.09.2026", "неизвестно", "неизвестно"),
])
def test_every_active_source_is_asked_and_deadline_reaches_submission(flags, answers):
    async def scenario():
        data = data_with_sources(flags)
        selected = [answer for answer, present in zip(answers, flags, strict=True) if present]
        known = [parse_answer("response_deadline", answer)["date"]
                 for answer in selected if answer != "неизвестно"]
        expected = None if not selected else {
            "date": min(known) if known else None,
            "precision": "EXACT" if known else "UNKNOWN",
        }
        async with harness() as (app, core, _sent, dispatch):
            core.draft.update(draftData=data, wizardState=next_missing_state(data))
            await dispatch(f"case:draft:{DRAFT}")
            for (_field, state), present, answer in zip(SOURCES, flags, answers, strict=True):
                if not present:
                    continue
                assert core.draft["wizardState"] == state
                await dispatch("text:" + answer)
                # Reload from the saved server cursor, not a gateway-only memory flag.
                await dispatch("/menu")
                assert gateway_bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
                await dispatch(f"case:draft:{DRAFT}")
            assert core.draft["wizardState"] == "CONFIRM"
            assert_wire_deadline(core.draft["draftData"], expected)

    asyncio.run(scenario())


def test_fresh_application_resumes_pending_source_instead_of_using_previous_deadline():
    async def scenario():
        async with harness() as (_app, core, _sent, dispatch):
            data = data_with_sources((True, True, True))
            core.draft.update(draftData=data, wizardState="CLAIM_DEADLINE")
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:30.09.2026")
            assert core.draft["wizardState"] == "LAWYER_DEADLINE"
            saved = deepcopy(core.draft)
        async with harness() as (_app, core, _sent, dispatch):
            core.draft = saved
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:28.09.2026")
            assert core.draft["wizardState"] == "AUTHORITY_DEADLINE"
            await dispatch("text:25.09.2026")
            assert core.draft["wizardState"] == "CONFIRM"
            assert_wire_deadline(core.draft["draftData"], {
                "date": "2026-09-25", "precision": "EXACT",
            })

    asyncio.run(scenario())


@pytest.mark.parametrize("raw", ["31.02.2026", "not a date", "", "2099-02-30"])
def test_invalid_deadline_keeps_source_cursor_and_previous_value(raw):
    async def scenario():
        async with harness() as (_app, core, _sent, dispatch):
            data = data_with_sources((True, True, True))
            data["response_deadline"] = {"date": "2026-09-30", "precision": "EXACT"}
            core.draft.update(draftData=data, wizardState="LAWYER_DEADLINE")
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:" + raw)
            assert core.draft["wizardState"] == "LAWYER_DEADLINE"
            assert not core.saved
            assert core.draft["draftData"]["response_deadline"] == data["response_deadline"]

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [
    "unknown", "неизвестно", {"date": "unknown", "precision": "EXACT"},
    {"date": "2026-07-02", "precision": []},
    {"date": None, "precision": "EXACT"},
])
def test_malformed_legacy_dates_do_not_count_as_known(value):
    assert not valid_value("claim_date", value)


@pytest.mark.parametrize("flags", COMBINATIONS)
@pytest.mark.parametrize("deadline", [
    {"date": "2026-09-30", "precision": "EXACT"},
    {"date": None, "precision": "UNKNOWN"},
])
def test_mapper_preserves_single_deadline_for_string_and_legacy_boolean_flags(flags, deadline):
    data = data_with_sources(flags)
    # Legacy drafts can have booleans, not only the modern three-state strings.
    for (field, _state), present in zip(SOURCES, flags, strict=True):
        data[field] = present
    if any(flags):
        data["response_deadline"] = deadline
    assert_wire_deadline(data, deadline if any(flags) else None)
