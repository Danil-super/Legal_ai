"""Urgent routing must survive unknown dates without inventing legal facts."""

import asyncio

import pytest
from legal_core.analysis_api import _analysis_date
from legal_core.api_contracts import FactInput
from legal_core.case_api import ApiError, _input_value
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from telegram_gateway import bot
from telegram_gateway.case_wizard import facts_from_v2_data
from telegram_gateway.factual_safety_intake import known_urgent_report
from test_case_experience import harness
from test_case_wizard import FakeQuery
from test_dialog_isolation import DRAFT
from test_factual_safety_gateway import runtime_context
from test_guided_case_intake_v2 import _base_data

URGENT_SIGNALS = [
    {"healthSignals": ["HOSPITALIZATION"]},
    {"incomingKind": "AUTHORITY_OR_COURT_DOCUMENT", "healthSignals": ["UNKNOWN"]},
]


@pytest.mark.parametrize("signal", URGENT_SIGNALS)
def test_guided_dispatcher_accepts_unknown_date_then_confirms_known_urgent_facts(signal):
    async def scenario():
        data = _base_data() | signal
        data.pop("eventDate")
        async with harness() as (_app, core, _sent, dispatch):
            core.draft.update(draftData=data, wizardState="EVENT_DATE")
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:неизвестно")
            assert core.draft["draftData"]["eventDate"] == {
                "date": None, "precision": "UNKNOWN",
            }
            assert core.draft["wizardState"] == "SAFETY"
            await dispatch("/menu")
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("case:safety:known:confirm")
            assert core.draft["wizardState"] == "SUMMARY"
            saved = core.draft["draftData"]
            assert saved["eventDate"] == {"date": None, "precision": "UNKNOWN"}
            assert all(saved["safetyScreening"][field] == "UNKNOWN" for field in SCREENING_FIELDS)
            assert saved["safetyScreening"]["amount"] == "UNKNOWN"
            await dispatch("case:v2:summary:confirm")
            assert core.draft["wizardState"] == "V2_CONFIRM"

    asyncio.run(scenario())


def test_unknown_date_allows_collecting_a_later_hospitalization_signal():
    async def scenario():
        data = _base_data()
        for field in ("eventDate", "conflictStage", "clinicActions", "healthSignals",
                      "caseMaterialsStatus"):
            data.pop(field)
        async with harness() as (_app, core, _sent, dispatch):
            core.draft.update(draftData=data, wizardState="EVENT_DATE")
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:неизвестно")
            assert core.draft["wizardState"] == "HEALTH"
            assert "conflictStage" not in core.draft["draftData"]
            assert "clinicActions" not in core.draft["draftData"]
            for callback in (
                "case:v2:single:conflictStage:ONGOING",
                "case:v2:multi:clinicActions:INVITED_FOR_EXAMINATION",
                "case:v2:multi:clinicActions:DONE",
                "case:v2:multi:healthSignals:HOSPITALIZATION",
                "case:v2:multi:healthSignals:DONE",
                "case:v2:material:skip",
                "case:safety:known:confirm",
                "case:v2:summary:confirm",
            ):
                await dispatch(callback)
            assert core.draft["wizardState"] == "V2_CONFIRM"
            assert core.draft["draftData"]["healthSignals"] == ["HOSPITALIZATION"]
            assert core.draft["draftData"]["eventDate"] == {
                "date": None, "precision": "UNKNOWN",
            }

    asyncio.run(scenario())


@pytest.mark.parametrize("signal", URGENT_SIGNALS)
def test_final_gateway_confirmation_submits_urgent_unknown_date_to_server(signal):
    data = _base_data() | signal | {
        "eventDate": {"date": None, "precision": "UNKNOWN"},
        "safetyScreening": {
            "schemaVersion": SCREENING_VERSION,
            **dict.fromkeys(SCREENING_FIELDS, "UNKNOWN"),
            "amount": "UNKNOWN",
        },
    }
    update, context, pipeline, _message = runtime_context(data, "case:v2:summary:confirm")
    update.callback_query = FakeQuery(f"case:confirm:{data['workflow_id']}")
    submitted = []
    original_submit = pipeline.submit_workflow

    async def capture(*args, **kwargs):
        submitted.append((args, kwargs))
        return await original_submit(*args, **kwargs)

    pipeline.submit_workflow = capture
    asyncio.run(bot.confirm_case(update, context))
    assert len(submitted) == 1
    args, kwargs = submitted[0]
    assert kwargs["intake_schema_version"] == "dental-case-intake.v2"
    facts = {item["factKey"]: item["value"] for item in args[1]}
    assert facts["EVENT_DATE"] == {"date": None, "precision": "UNKNOWN"}
    assert facts["FACTUAL_SAFETY_SCREENING"] == data["safetyScreening"]
    assert "FORMAL_CLAIM" not in facts and "HARM_CLAIMED" not in facts


@pytest.mark.parametrize("amount, expected", [(999_999, False), (1_000_000, True),
                                              (1_000_001, True)])
def test_monetary_shortcut_uses_active_10k_inclusive_boundary(amount, expected):
    data = _base_data() | {
        "safetyScreening": {
            "schemaVersion": SCREENING_VERSION,
            "moneyRequested": "YES",
            "amount": {"amountKopecks": amount, "currency": "RUB"},
        },
    }
    assert known_urgent_report(data) is expected


@pytest.mark.parametrize("signal", URGENT_SIGNALS)
def test_urgent_unknown_date_still_cannot_select_a_legal_revision(signal):
    data = _base_data() | signal | {"eventDate": {"date": None, "precision": "UNKNOWN"}}
    inputs = [FactInput.model_validate(item) for item in facts_from_v2_data(data)]
    facts = {item.fact_key: _input_value(item.value_type, item.value) for item in inputs}
    with pytest.raises(ApiError) as raised:
        _analysis_date(facts)
    assert raised.value.code == "ANALYSIS_DATE_UNCERTAIN"
