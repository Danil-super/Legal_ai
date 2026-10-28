"""Real PTB + Core HTTP + PostgreSQL preserve an out-of-order partial answer."""

import asyncio
import os
from uuid import UUID, uuid4

import httpx2
import pytest
from legal_core.intake_answer_contracts import AnswerInterpretation
from legal_core.intake_answer_lifecycle import complete_answer
from sqlalchemy import text
from telegram_gateway import bot
from telegram_gateway.analysis_jobs_runtime import JOBS_CLIENT_KEY, AnalysisJobsClient
from telegram_gateway.case_wizard import LegalCoreClient
from test_answer_http_postgres_flow import HeldDispatch, full_harness
from test_case_api import actor_headers
from test_intake_preparation_lifecycle import _run
from test_report_free_workflow import isolated_database as isolated_database
from test_report_free_workflow import preparation_workflow as preparation_workflow
from test_secondary_factual_staging import ANSWER, DATE, PURPOSE_UNKNOWN, partial_bundle

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("unknown_date", [False, True])
@pytest.mark.parametrize("simplified", [True, False], ids=["single-summary", "legacy"])
def test_partial_secondary_date_is_staged_once_and_not_requested_after_payment(
    preparation_workflow, monkeypatch, unknown_date, simplified,
):
    monkeypatch.setenv("AI_FIRST_FACTUAL_INTAKE_ENABLED", "true")
    monkeypatch.setattr("telegram_gateway.intake_delivery.POLL_INTERVAL", 0.01)
    core, actor, _clinic, _membership, engine = preparation_workflow
    runner = HeldDispatch()
    core.app.state.intake_preparation_runner = runner
    initial, *_ = partial_bundle()
    source = "Дата услуги неизвестна" if unknown_date else ANSWER
    date_value = {"date": None, "precision": "UNKNOWN"} if unknown_date else DATE
    created = core.post("/v1/telegram-intake-drafts", headers=actor_headers(actor, uuid4()),
                        json={"intakeSchemaVersion": "dental-case-intake.v2"}).json()
    draft_id = UUID(created["id"])
    seeded = core.put(f"/v1/telegram-intake-drafts/{draft_id}",
                      headers=actor_headers(actor, uuid4()), json={
                          "expectedRevision": created["revision"],
                          "wizardState": "ANALYSIS_CLARIFICATION", "draftData": initial,
                      })
    assert seeded.status_code == 200, seeded.text
    submissions = []

    def bridge(request):
        actual = core.request(request.method, str(request.url), headers=dict(request.headers),
                              content=request.content)
        if request.url.path.endswith("/submissions"):
            submissions.append(actual.status_code)
        return httpx2.Response(actual.status_code, content=actual.content,
                               headers={"Content-Type": "application/json"})

    def saved():
        response = core.get(f"/v1/telegram-intake-drafts/{draft_id}", headers=actor_headers(actor))
        assert response.status_code == 200, response.text
        return response.json()

    async def scenario():
        async with (full_harness(simplified=simplified) as (app, _fake, sent, dispatch),
                    httpx2.AsyncClient(base_url="http://core.test",
                                       transport=httpx2.MockTransport(bridge)) as http):
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(http)
            app.bot_data[JOBS_CLIENT_KEY] = AnalysisJobsClient(http, "synthetic-internal-key")

            async def send(value):
                await dispatch(value, actor=actor)

            async def delivered():
                async def done():
                    while (actor, "answer_interpretation") in app.bot_data.get(
                        "intake_delivery_tasks", {},
                    ):
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(done(), timeout=8)

            await send(f"case:draft:{draft_id}")
            assert "Как оплачивалась" in sent[-1]["text"]
            assert "Когда была оказана" in sent[-1]["text"]
            await send("text:" + source)
            assert len(runner.claims) == 1
            _owner, claim = runner.claims[-1]
            *_, raw = partial_bundle(operation=claim.operation_id, revision=saved()["revision"],
                                     data=initial, answer=source)
            raw["factualProposal"]["candidates"][0]["value"] = date_value
            await asyncio.to_thread(_run, lambda sessions: complete_answer(
                sessions, telegram_user_id=actor, operation_id=claim.operation_id,
                lease_token=claim.lease_token,
                interpretation=AnswerInterpretation.model_validate(raw),
            ))
            await delivered()
            after_date = saved()
            if not simplified:
                assert after_date == seeded.json()
                assert not submissions
                assert not any(button.callback_data.startswith("answer:a1:")
                               for row in sent[-1]["reply_markup"].inline_keyboard
                               for button in row)
                return
            assert after_date["draftData"]["analysisClarificationAnswers"]["answers"] == [
                PURPOSE_UNKNOWN,
                {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0, "value": date_value},
            ]
            assert "Как оплачивалась" in sent[-1]["text"]
            assert "Когда была оказана" not in sent[-1]["text"]
            assert "Что клинике нужно сделать" not in sent[-1]["text"]
            assert not submissions
            await send("text:Платно")
            assert len(runner.claims) == 2
            _owner, payment = runner.claims[-1]
            await asyncio.to_thread(_run, lambda sessions: complete_answer(
                sessions, telegram_user_id=actor, operation_id=payment.operation_id,
                lease_token=payment.lease_token,
                interpretation=AnswerInterpretation.model_validate({
                    "status": "PROPOSED", "value": "PAID", "sourceSpans": [{"start": 0, "end": 6}],
                }),
            ))
            await delivered()
            after_payment = saved()
            assert after_payment["draftData"]["analysisClarificationAnswers"]["answers"][:2] == (
                after_date["draftData"]["analysisClarificationAnswers"]["answers"]
            )
            assert app.user_data[actor][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SUMMARY"
            buttons = [button.callback_data for row in sent[-1]["reply_markup"].inline_keyboard
                       for button in row]
            assert buttons.count("case:v2:summary:confirm") == 1
            assert not submissions
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM cases")) == 0
                assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0
                assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE "
                                              "action='INTAKE_ANSWER_ACCEPTED'")) == 0
            await send("case:v2:summary:confirm")
            assert submissions == [201]
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM cases")) == 1
                assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0
    asyncio.run(scenario())
