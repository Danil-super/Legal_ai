"""Real PTB→Gateway HTTP→Core→PostgreSQL; Telegram/model delivery alone is synthetic."""

import asyncio
import json
import os
from copy import deepcopy
from uuid import UUID

import httpx2
import pytest
from legal_core.intake_preparation import complete_preparation
from legal_core.intake_preparation_contracts import DraftSuggestions
from sqlalchemy import text
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreClient
from telegram_gateway.factual_safety_intake import screening_question
from telegram_gateway.understanding_intent import bound_callback
from test_case_api import actor_headers
from test_confirmed_understanding_runtime import active_harness
from test_intake_preparation_lifecycle import _run
from test_report_free_workflow import isolated_database as isolated_database
from test_report_free_workflow import preparation_workflow as preparation_workflow

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)
SOURCE = "Синтетический посетитель недоволен ожиданием приёма в клинике."
UNKNOWN_DATE = {"date": None, "precision": "UNKNOWN"}


class HeldSyntheticDispatch:
    """Keep a real reserved operation pending until the test explicitly completes it."""

    def __init__(self):
        self.claims = []

    async def submit(self, *, telegram_user_id, operation):
        self.claims.append((telegram_user_id, operation))


@pytest.mark.parametrize("entry", ["quick", "guided"])
def test_active_both_entries_reach_one_report_free_case_through_real_http_and_postgres(
    preparation_workflow,
    monkeypatch,
    entry,
):
    core, actor, _, _, engine = preparation_workflow
    runner = HeldSyntheticDispatch()
    core.app.state.intake_preparation_runner = runner
    for target in ("build_intake_report", "render_report_pdf"):
        monkeypatch.setattr(
            f"legal_core.case_api.{target}",
            lambda *args, **kwargs: pytest.fail("no intermediate report/PDF"),
        )
    exchanges, submissions = [], []

    def bridge(request):
        assert not request.url.path.endswith("/pdf")
        actual = core.request(
            request.method, str(request.url), headers=dict(request.headers), content=request.content
        )
        body = json.loads(request.content) if request.content else None
        exchanges.append((request.method, request.url.path, body, actual.status_code))
        if request.url.path.endswith("/submissions"):
            submissions.append((body, actual.status_code, actual.json()))
        return httpx2.Response(
            actual.status_code, content=actual.content, headers={"Content-Type": "application/json"}
        )

    def counts():
        with engine.connect() as connection:
            return tuple(
                connection.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in (
                    "cases",
                    "case_facts",
                    "case_reports",
                    "analysis_jobs",
                )
            )

    async def scenario():
        async with (
            active_harness() as (app, _fake_core, sent, dispatch),
            httpx2.AsyncClient(
                base_url="http://core.test", transport=httpx2.MockTransport(bridge)
            ) as http,
        ):
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(http)

            async def send(value):
                await dispatch(value, actor=actor)

            if entry == "quick":
                await send("quick:start")
                await send("text:" + SOURCE)
                assert len(runner.claims) == 1
                claimed_actor, claim = runner.claims[0]
                assert claimed_actor == actor and claim.state == "PENDING"
                draft_id = claim.intent.draft_id
                saved = core.get(
                    f"/v1/telegram-intake-drafts/{draft_id}", headers=actor_headers(actor)
                ).json()
                assert saved["draftData"] == {
                    "intakeVersion": 2,
                    "eventSummary": SOURCE,
                    "incomingSourceStatus": "NOT_ATTACHED",
                }
                assert counts() == (0, 0, 0, 0)
                # The real lifecycle sees source saved by Telegram, not a pre-seeded draft.
                proposed = DraftSuggestions.model_validate(
                    {
                        "incomingKind": "COMPLAINT",
                        "eventDate": UNKNOWN_DATE,
                        "sourceSpans": {
                            field: [{"start": 0, "end": 13}]
                            for field in ("incomingKind", "eventDate")
                        },
                    }
                )
                await asyncio.to_thread(
                    _run,
                    lambda sessions: complete_preparation(
                        sessions,
                        telegram_user_id=actor,
                        operation_id=claim.operation_id,
                        lease_token=claim.lease_token,
                        suggestions=proposed,
                    ),
                )
                await send(bound_callback("refresh", claim.operation_id, draft_id))
                unchanged = core.get(
                    f"/v1/telegram-intake-drafts/{draft_id}", headers=actor_headers(actor)
                ).json()
                assert unchanged == saved
                assert not submissions and counts() == (0, 0, 0, 0)
                await send(bound_callback("accept", claim.operation_id, draft_id))
                accepted = core.get(
                    f"/v1/telegram-intake-drafts/{draft_id}", headers=actor_headers(actor)
                ).json()
                assert accepted["revision"] == saved["revision"] + 1
                assert accepted["draftData"]["eventDate"] == UNKNOWN_DATE
                assert accepted["draftData"]["incomingKind"] == "COMPLAINT"
                assert counts() == (0, 0, 0, 0)
                # Losing process-local selectors cannot lose the confirmed source/facts.
                app.user_data[actor].clear()
                await send(f"case:draft:{draft_id}")
            else:
                await send("case:start")
                draft_id = UUID(app.user_data[actor][bot.WIZARD_DATA_KEY][bot.DRAFT_ID_KEY])
                await send("case:v2:single:incomingKind:COMPLAINT")
            data = app.user_data[actor][bot.WIZARD_DATA_KEY]
            assert data[bot.DRAFT_STATE_KEY] == "SITUATION"
            for value in (
                "case:v2:multi:situationAreas:SERVICE",
                "case:v2:multi:situationAreas:DONE",
            ):
                await send(value)
            if entry == "guided":
                await send("text:" + SOURCE)
                await send("text:неизвестно")
            assert data[bot.DRAFT_STATE_KEY] == "HEALTH"
            # These descriptions did not supply optional clinic context. The
            # normal route must not force or manufacture either statement.
            assert "conflictStage" not in data and "clinicActions" not in data
            for value in (
                "case:v2:multi:healthSignals:UNKNOWN",
                "case:v2:multi:healthSignals:DONE",
            ):
                await send(value)
            for _ in range(8):
                pending = screening_question(data)
                if pending is None:
                    break
                field, _prompt = pending
                answer = "UNKNOWN" if field == "representativeContact" else "NO"
                await send(f"case:safety:{field}:{answer}")
            assert data[bot.DRAFT_STATE_KEY] == "ANALYSIS_CLARIFICATION"
            await send("case:v2:clarify:RESPONSE_PURPOSE:global:UNKNOWN")
            assert data[bot.DRAFT_STATE_KEY] == "SUMMARY"
            assert data["eventDate"] == UNKNOWN_DATE
            assert data["safetyScreening"]["representativeContact"] == "UNKNOWN"
            confirmed = deepcopy(bot._draft_payload(data))
            assert not submissions and counts() == (0, 0, 0, 0)
            await send("case:v2:summary:confirm")
            callbacks = [
                button.callback_data
                for row in sent[-1]["reply_markup"].inline_keyboard
                for button in row
            ]
            assert f"case:confirm2:{draft_id}" in callbacks
            assert counts() == (0, 0, 0, 0)
            await send(f"case:confirm2:{draft_id}")
            assert len(submissions) == 1, exchanges
            assert submissions[0][1] == 201
            receipt = submissions[0][2]
            assert receipt["receiptVersion"] == 2 and "report" not in receipt
            assert bot.WIZARD_DATA_KEY not in app.user_data[actor]
            # The old Telegram confirmation is a read-only recovery, not another case.
            await send(f"case:confirm2:{draft_id}")
            assert len(submissions) == 1
            assert not any("document" in item for item in sent)
            assert "Hermes" not in "\n".join(item.get("text", "") for item in sent)
            with engine.connect() as connection:
                facts = dict(
                    connection.execute(text("SELECT fact_key,value_json FROM case_facts")).all()
                )
                assert facts["EVENT_SUMMARY"] == {"text": SOURCE}
                assert facts["EVENT_DATE"] == UNKNOWN_DATE
                assert facts["FACTUAL_SAFETY_SCREENING"] == confirmed["safetyScreening"]
                assert facts["HEALTH_CONSEQUENCE_SIGNALS"] == {"values": ["UNKNOWN"]}
                assert facts["ANALYSIS_CLARIFICATION_ANSWERS"] == {"answers": [
                    {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "UNKNOWN"},
                ]}
                assert "CONFLICT_STAGE" not in facts and "CLINIC_ACTIONS" not in facts
                assert (
                    connection.scalar(text("SELECT receipt_version FROM telegram_case_workflows"))
                    == 2
                )
            assert counts() == (1, len(submissions[0][0]["facts"]), 0, 0)
            assert len(runner.claims) == (1 if entry == "quick" else 0)

    asyncio.run(scenario())
