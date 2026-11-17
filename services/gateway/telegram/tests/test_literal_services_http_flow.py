"""A literal service list advances through real PTB, Agent validation and Core PostgreSQL."""

import asyncio
import os
from uuid import UUID, uuid4

import httpx2
import pytest
from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding
from legal_core.intake_answer_contracts import AnswerInterpretation
from legal_core.intake_answer_lifecycle import complete_answer, read_answer_context
from sqlalchemy import text
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreClient
from test_answer_http_postgres_flow import HeldDispatch, full_harness
from test_case_api import actor_headers
from test_intake_preparation_lifecycle import _run
from test_report_free_workflow import isolated_database as isolated_database
from test_report_free_workflow import preparation_workflow as preparation_workflow

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("availability", ["unresolved", "unavailable"])
@pytest.mark.parametrize("already_stuck", [False, True], ids=["new-answer", "old-unresolved"])
def test_service_list_advances_without_a_repeated_question_even_if_model_cannot_interpret_it(
    preparation_workflow, monkeypatch, availability, already_stuck,
):
    monkeypatch.setattr("telegram_gateway.intake_delivery.POLL_INTERVAL", 0.01)
    core, actor, _clinic, _membership, engine = preparation_workflow
    runner = HeldDispatch()
    core.app.state.intake_preparation_runner = runner
    created = core.post("/v1/telegram-intake-drafts", headers=actor_headers(actor, uuid4()),
                        json={"intakeSchemaVersion": "dental-case-intake.v2"}).json()
    draft_id = UUID(created["id"])
    initial = {
        "intakeVersion": 2, "incomingKind": "COMPLAINT",
        "incomingSourceStatus": "NOT_ATTACHED", "situationAreas": ["TREATMENT"],
        "eventSummary": "Синтетический спор: оплатили дополнительные медицинские услуги.",
    }
    seed = core.put(f"/v1/telegram-intake-drafts/{draft_id}",
                    headers=actor_headers(actor, uuid4()), json={
                        "expectedRevision": created["revision"],
                        "wizardState": "SERVICES", "draftData": initial,
                    })
    assert seed.status_code == 200, seed.text

    def saved():
        response = core.get(f"/v1/telegram-intake-drafts/{draft_id}", headers=actor_headers(actor))
        assert response.status_code == 200, response.text
        return response.json()

    def bridge(request):
        result = core.request(request.method, str(request.url), headers=dict(request.headers),
                              content=request.content)
        return httpx2.Response(result.status_code, content=result.content,
                               headers={"Content-Type": "application/json"})

    class Model:
        async def complete_json(self, **kwargs):
            if availability == "unavailable":
                from agent_orchestrator.hermes_client import HermesUnavailable

                raise HermesUnavailable()
            return {"status": "UNRESOLVED", "value": None, "sourceSpans": [],
                    "reasonCode": "AMBIGUOUS"}

    async def scenario():
        async with (full_harness(simplified=True) as (app, _fake, sent, dispatch),
                    httpx2.AsyncClient(base_url="http://core.test",
                                       transport=httpx2.MockTransport(bridge)) as http):
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(http)
            async def delivered():
                while (actor, "answer_interpretation") in app.bot_data.get(
                    "intake_delivery_tasks", {},
                ):
                    await asyncio.sleep(0.01)

            await dispatch(f"case:draft:{draft_id}", actor=actor)
            assert "Какие услуги" in sent[-1]["text"]
            if already_stuck:
                await dispatch("text:Пока не могу уточнить названия", actor=actor)
                _claimed_actor, previous = runner.claims[-1]
                await asyncio.to_thread(_run, lambda sessions: complete_answer(
                    sessions, telegram_user_id=actor, operation_id=previous.operation_id,
                    lease_token=previous.lease_token,
                    interpretation=AnswerInterpretation(status="UNRESOLVED", value=None,
                        sourceSpans=[], reasonCode="AMBIGUOUS"),
                ))
                await asyncio.wait_for(delivered(), timeout=8)
                assert saved()["wizardState"] == "SERVICES"
            await dispatch("text:Лечение кариеса; профессиональная гигиена", actor=actor)
            assert len(runner.claims) == 1 + int(already_stuck)
            _claimed_actor, claim = runner.claims[-1]
            captured = await asyncio.to_thread(_run, lambda sessions: read_answer_context(
                sessions, telegram_user_id=actor, operation_id=claim.operation_id,
                lease_token=claim.lease_token, expected_revision=claim.intent.draft_revision,
                expected_input_sha256=claim.intent.input_sha256,
            ))
            proposal = await AnswerUnderstanding(researcher=Model()).interpret(
                captured.answer, captured.question, captured.confirmed_context,
                captured.as_of_date, retained_sources=captured.retained_sources,
                allowed_questions=captured.allowed_questions,
            )
            await asyncio.to_thread(_run, lambda sessions: complete_answer(
                sessions, telegram_user_id=actor, operation_id=claim.operation_id,
                lease_token=claim.lease_token, interpretation=proposal,
            ))

            await asyncio.wait_for(delivered(), timeout=8)
            final = saved()
            assert final["draftData"]["affectedServices"] == [
                "Лечение кариеса", "профессиональная гигиена",
            ]
            assert final["wizardState"] == "EVENT_DATE"
            assert "Когда пациент обратился" in sent[-1]["text"]
            assert "Какие услуги" not in sent[-1]["text"]
            assert final["draftData"]["eventSummary"] == initial["eventSummary"]
            assert not any(key in final["draftData"] for key in (
                "eventDate", "safetyScreening", "analysisClarificationAnswers",
            ))
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM cases")) == 0
                assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0
                assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE "
                                              "action='INTAKE_ANSWER_ACCEPTED'")) == 0
    asyncio.run(scenario())
