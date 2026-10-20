"""Both real PTB entries interpret later answers through real Core HTTP and PostgreSQL."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx2
import pytest
from legal_core.intake_answer_contracts import AnswerInterpretation
from legal_core.intake_answer_lifecycle import complete_answer
from legal_core.intake_preparation import complete_preparation
from legal_core.intake_preparation_contracts import DraftSuggestions
from sqlalchemy import text
from telegram_gateway import bot
from telegram_gateway.analysis_jobs_runtime import JOBS_CLIENT_KEY, AnalysisJobsClient
from telegram_gateway.answer_intent import bound_callback
from telegram_gateway.case_wizard import LegalCoreClient
from telegram_gateway.factual_safety_intake import screening_question
from telegram_gateway.guided_case_intake_v2 import next_clarification_request
from telegram_gateway.understanding_intent import bound_callback as initial_callback
from test_case_api import actor_headers
from test_case_experience import harness
from test_intake_preparation_lifecycle import _run
from test_report_free_workflow import isolated_database as isolated_database
from test_report_free_workflow import preparation_workflow as preparation_workflow

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL",
)
SOURCE = "Синтетический посетитель недоволен ожиданием приёма в клинике."
UNKNOWN_DATE = {"date": None, "precision": "UNKNOWN"}


class HeldDispatch:
    def __init__(self):
        self.claims, self.cancelled = [], []

    async def submit(self, *, telegram_user_id, operation):
        self.claims.append((telegram_user_id, operation))

    def cancel(self, operation_id):
        self.cancelled.append(operation_id)


@asynccontextmanager
async def full_harness(*, simplified=False):
    import test_case_experience as original
    factory = original.build_application_with_case_experience
    with patch.object(original, "build_application_with_case_experience", lambda token: factory(
        token, enable_understanding=True, enable_answer_interpretation=True,
        enable_simplified_dialogue=simplified,
    )):
        async with harness() as items:
            yield items


@pytest.mark.parametrize("entry", ["quick-service", "guided-treatment"])
@pytest.mark.parametrize("simplified", [False, True], ids=["per-answer", "single-summary"])
def test_both_entries_confirm_every_later_free_answer_before_one_report_free_case(
    preparation_workflow, monkeypatch, entry, simplified,
):
    monkeypatch.setattr("telegram_gateway.intake_delivery.POLL_INTERVAL", 0.01)
    core, actor, _, _, engine = preparation_workflow
    runner = HeldDispatch()
    core.app.state.intake_preparation_runner = runner
    for target in ("build_intake_report", "render_report_pdf"):
        monkeypatch.setattr(f"legal_core.case_api.{target}", lambda *args, **kwargs: pytest.fail(
            "no intermediate report or PDF",
        ))
    exchanges, submissions, interpreted_states = [], [], []

    def bridge(request):
        assert not request.url.path.endswith("/pdf")
        actual = core.request(request.method, str(request.url), headers=dict(request.headers),
                              content=request.content)
        body = json.loads(request.content) if request.content else None
        exchanges.append((request.method, request.url.path, body, actual.status_code))
        if request.url.path.endswith("/submissions"):
            submissions.append((body, actual.status_code, actual.json()))
        return httpx2.Response(actual.status_code, content=actual.content,
                               headers={"Content-Type": "application/json"})

    def counts():
        with engine.connect() as connection:
            return tuple(connection.scalar(text(f"SELECT count(*) FROM {table}")) for table in (
                "cases", "case_facts", "case_reports", "analysis_jobs",
            ))

    def saved(draft_id):
        response = core.get(f"/v1/telegram-intake-drafts/{draft_id}", headers=actor_headers(actor))
        assert response.status_code == 200, response.text
        return response.json()

    async def scenario():
        async with (full_harness(simplified=simplified) as (app, _fake, sent, dispatch),
                    httpx2.AsyncClient(
            base_url="http://core.test", transport=httpx2.MockTransport(bridge),
        ) as http):
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(http)
            if simplified:
                app.bot_data[JOBS_CLIENT_KEY] = AnalysisJobsClient(http, "synthetic-internal-key")

            async def send(value):
                await dispatch(value, actor=actor)

            async def delivered(selection):
                async def done():
                    while (actor, selection) in app.bot_data.get("intake_delivery_tasks", {}):
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(done(), timeout=8)

            if entry == "quick-service":
                await send("quick:start")
                await send("text:" + SOURCE)
                assert len(runner.claims) == 1
                claimed_actor, claim = runner.claims[-1]
                assert claimed_actor == actor and claim.state == "PENDING"
                draft_id = claim.intent.draft_id
                original = saved(draft_id)
                assert original["draftData"]["eventSummary"] == SOURCE
                await asyncio.to_thread(_run, lambda sessions: complete_preparation(
                    sessions, telegram_user_id=actor, operation_id=claim.operation_id,
                    lease_token=claim.lease_token, suggestions=DraftSuggestions.model_validate({
                        "incomingKind": "COMPLAINT",
                        "sourceSpans": {"incomingKind": [{"start": 0, "end": len(SOURCE)}]},
                    }),
                ))
                if simplified:
                    await delivered("understanding_intake")
                    assert saved(draft_id)["draftData"]["incomingKind"] == "COMPLAINT"
                else:
                    await send(initial_callback("refresh", claim.operation_id, draft_id))
                    assert saved(draft_id) == original and counts() == (0, 0, 0, 0)
                    await send(initial_callback("accept", claim.operation_id, draft_id))
                app.user_data[actor].clear()
                await send(f"case:draft:{draft_id}")
            else:
                await send("case:start")
                draft_id = UUID(app.user_data[actor][bot.WIZARD_DATA_KEY][bot.DRAFT_ID_KEY])

            async def interpret(source, value, expected_state):
                before = saved(draft_id)
                claim_count = len(runner.claims)
                await send("text:" + source)
                assert len(runner.claims) == claim_count + 1, exchanges[-5:]
                claimed_actor, claim = runner.claims[-1]
                assert claimed_actor == actor and claim.response.state == "PENDING"
                assert claim.intent.operation_kind == "INTERPRET_ANSWER"
                assert claim.intent.draft_id == draft_id
                reserved = saved(draft_id)
                assert reserved["draftData"] == before["draftData"]
                assert reserved["wizardState"] == expected_state
                await asyncio.to_thread(_run, lambda sessions: complete_answer(
                    sessions, telegram_user_id=actor, operation_id=claim.operation_id,
                    lease_token=claim.lease_token,
                    interpretation=AnswerInterpretation.model_validate({
                        "status": "PROPOSED", "value": value,
                        "sourceSpans": [{"start": 0, "end": len(source)}],
                    }),
                ))
                if simplified:
                    await delivered("answer_interpretation")
                else:
                    await send(bound_callback("refresh", claim.operation_id, draft_id))
                    assert saved(draft_id) == reserved
                    assert counts() == (0, 0, 0, 0) and not submissions
                    await send(bound_callback("accept", claim.operation_id, draft_id))
                accepted = saved(draft_id)
                assert accepted["revision"] > reserved["revision"]
                if not simplified:
                    await send(bound_callback("accept", claim.operation_id, draft_id))
                    # Receipt replay never reapplies facts or advances navigation again.
                    assert saved(draft_id) == accepted
                with engine.connect() as connection:
                    assert connection.scalar(text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE action='INTAKE_ANSWER_ACCEPTED' AND resource_id=:id"
                    ), {"id": claim.operation_id}) == (0 if simplified else 1)
                interpreted_states.append(expected_state)
                assert counts() == (0, 0, 0, 0)

            base_answers = {
                "INCOMING": ("Пациент недоволен", "COMPLAINT"),
                "SITUATION": ("Лечение и обслуживание", ["TREATMENT", "SERVICE"])
                if entry == "guided-treatment" else ("Обслуживание", ["SERVICE"]),
                "SERVICES": ("установка коронки", ["установка коронки"]),
                "EVENT": (SOURCE, SOURCE),
                "EVENT_DATE": ("Точную дату не знаю", UNKNOWN_DATE),
                "CHRONOLOGY": ("Это первое обращение", "FIRST"),
                "CLINIC_ACTIONS": ("Клиника пока ничего не предприняла", ["NOTHING_YET"]),
                "HEALTH": ("Ухудшение здоровья не известно", ["NO_KNOWN_INFORMATION"]),
            }
            service_requests_added = False
            for _ in range(30):
                data = app.user_data[actor][bot.WIZARD_DATA_KEY]
                state = data[bot.DRAFT_STATE_KEY]
                if state == "SUMMARY":
                    break
                if state == "MATERIALS":
                    await send("case:v2:material:skip")
                    continue
                if state == "SAFETY":
                    field, _prompt = screening_question(data)
                    if field == "amount":
                        source, value = "Просит 1200,50 рублей", {
                            "amountKopecks": 120050, "currency": "RUB"}
                    else:
                        value = "UNKNOWN" if field == "representativeContact" else (
                            "YES" if field == "moneyRequested" and entry == "guided-treatment"
                            else "NO"
                        )
                        source = {"YES": "Да, просит вернуть деньги", "NO": "Нет",
                                  "UNKNOWN": "Неизвестно"}[value]
                elif state == "ANALYSIS_CLARIFICATION":
                    request = next_clarification_request(data)
                    source, value = {
                        "SERVICE_PAYMENT_BASIS": ("Услуга была платной", "PAID"),
                        "SERVICE_PROVIDED_DATE": ("Дату этой услуги не помню", UNKNOWN_DATE),
                        "RESPONSE_PURPOSE": ("Нужно ответить на просьбу", "ANSWER_REQUEST"),
                    }[request.question_id]
                else:
                    source, value = base_answers[state]
                await interpret(source, value, state)
                if state == "SERVICES" and not service_requests_added:
                    # Simulate Core's known scoped clarification checkpoint using the
                    # actual existing owned draft API, never a client/model question field.
                    current = saved(draft_id)
                    updated = core.put(f"/v1/telegram-intake-drafts/{draft_id}",
                        headers=actor_headers(actor, uuid4()), json={
                            "expectedRevision": current["revision"],
                            "wizardState": current["wizardState"], "draftData": {
                                **current["draftData"], "analysisClarificationRequests": [
                                    {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
                                    {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0},
                                    {"questionId": "RESPONSE_PURPOSE"},
                                ],
                            },
                        })
                    assert updated.status_code == 200, updated.text
                    service_requests_added = True
                    await send(f"case:draft:{draft_id}")
            else:
                pytest.fail("interpreted intake did not reach factual summary")
            data = app.user_data[actor][bot.WIZARD_DATA_KEY]
            assert data[bot.DRAFT_STATE_KEY] == "SUMMARY"
            assert data["eventDate"] == UNKNOWN_DATE
            assert data["safetyScreening"]["representativeContact"] == "UNKNOWN"
            assert interpreted_states.count("EVENT_DATE") == 1
            if entry == "guided-treatment":
                assert interpreted_states.count("ANALYSIS_CLARIFICATION") == 3
                answers = data["analysisClarificationAnswers"]["answers"]
                assert [item["value"] for item in answers] == [
                    "PAID", UNKNOWN_DATE, "ANSWER_REQUEST",
                ]
                assert data["safetyScreening"]["amount"] == {
                    "amountKopecks": 120050, "currency": "RUB"}
            confirmed = deepcopy(bot._draft_payload(data))
            assert not submissions and counts() == (0, 0, 0, 0)
            await send("case:v2:summary:confirm")
            if not simplified:
                assert any(button.callback_data == f"case:confirm2:{draft_id}"
                           for row in sent[-1]["reply_markup"].inline_keyboard for button in row)
                assert not submissions and counts() == (0, 0, 0, 0)
                await send(f"case:confirm2:{draft_id}")
            assert len(submissions) == 1, exchanges[-10:]
            assert submissions[0][1] == 201
            receipt = submissions[0][2]
            assert receipt["receiptVersion"] == 2 and "report" not in receipt
            await send(f"case:confirm2:{draft_id}")
            assert len(submissions) == 1
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM cases")) == 1
                assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0
                assert connection.scalar(text("SELECT count(*) FROM analysis_jobs")) == (
                    1 if simplified else 0
                )
                assert connection.scalar(text(
                    "SELECT value_json FROM case_facts WHERE fact_key='EVENT_DATE'"
                )) == UNKNOWN_DATE
                actual = connection.scalar(text(
                    "SELECT metadata_json FROM audit_events "
                    "WHERE action='TELEGRAM_WORKFLOW_SUCCEEDED'"
                ))
                assert actual["receiptVersion"] == 2
            assert confirmed["eventDate"] == UNKNOWN_DATE

    asyncio.run(scenario())
