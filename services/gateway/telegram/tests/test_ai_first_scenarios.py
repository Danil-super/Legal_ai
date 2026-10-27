"""AIF01 desired-behavior reproductions, not a claim that AI-first is implemented.

Telegram/Core persistence and model HTTP are boundary fakes. The actual Agent
serializer/validator, Core value application, PTB dispatcher and question selector
run. No mocked provider is credited with extracting fields its contract cannot carry.
"""

import asyncio
import json
from datetime import date

import httpx
from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_factual_candidates import allowed_factual_questions, retained_answer_sources
from telegram_gateway import bot
from telegram_gateway.understanding_client import canonical_hash
from test_answer_runtime import AnswerCore
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import simplified_harness
from test_intake_transient_cleanup import delivery_finished

AS_OF = date(2026, 10, 26)
NARRATIVE = (
    "Вымышленный пример: после установки коронки посетитель обратился 15.10.2026. "
    "Клиника предложила осмотр. Посетитель просит вернуть 5000 рублей."
)


async def interpret_at_http_boundary(source, data, response, calls, *, retained=None):
    question = derive_answer_question(data, "SERVICES")
    assert question is not None

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": json.dumps(response)}, "finish_reason": "stop"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        researcher = HermesClient(HermesEndpoint("http://synthetic-researcher:8642", "test-only",
                                  model="existing-researcher", timeout_seconds=65), client=http)
        return await AnswerUnderstanding(researcher=researcher).interpret(
            source, question, data, AS_OF, **({
                "retained_sources": retained,
                "allowed_questions": allowed_factual_questions(data, question),
            } if retained is not None else {}),
        )


def test_confirmed_narrative_remains_in_the_actual_model_request_for_followup_context():
    async def run():
        calls = []
        data = {"intakeVersion": 2, "situationAreas": ["TREATMENT"],
                "eventSummary": NARRATIVE}
        await interpret_at_http_boundary("Как указано выше", data, {
            "status": "UNRESOLVED", "value": None, "sourceSpans": [],
            "reasonCode": "AMBIGUOUS",
        }, calls)
        sent_context = json.dumps(json.loads(calls[0]["messages"][1]["content"]),
                                  ensure_ascii=False)
        assert "коронки" in sent_context and "15.10.2026" in sent_context, (
            "the actual model request must retain the service/date already supplied "
            "in the narrative; availability does not prove the model resolves references"
        )

    asyncio.run(run())


def test_rich_followup_does_not_reask_a_date_already_in_the_same_answer():
    async def run():
        async with simplified_harness() as (app, core, sent, dispatch):
            data = {"intakeVersion": 2, "incomingKind": "COMPLAINT",
                    "incomingSourceStatus": "NOT_ATTACHED", "situationAreas": ["TREATMENT"],
                    "eventSummary": "Вымышленный посетитель недоволен услугой клиники."}
            core.draft.update(wizardState="SERVICES", draftData=data)
            await dispatch(f"case:draft:{DRAFT}")
            answer = ("Установка коронки. Обращение было 15.10.2026. "
                      "Требует 5000 рублей. Пригласили на осмотр.")
            await dispatch("text:" + answer)
            operation = core.starts[-1]
            sources = retained_answer_sources(operation, core.draft["revision"], data, answer)
            event = derive_answer_question(data, "EVENT_DATE")
            start = answer.index("15.10.2026")
            proposal = await interpret_at_http_boundary(answer, data, {
                "status": "PROPOSED", "value": ["Установка коронки"],
                "sourceSpans": [{"start": 0, "end": len("Установка коронки")}],
                "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1",
                    "candidates": [{"question": event.model_dump(mode="json", by_alias=True),
                        "value": {"date": "2026-10-15", "precision": "EXACT"}, "anchors": [{
                            "sourceId": str(sources[-1].source_id),
                            "sourceSha256": sources[-1].source_sha256,
                            "start": start, "end": start + len("15.10.2026"),
                        }]}]},
            }, [], retained=sources)
            before = len(sent)
            # Publish exactly the validated current model response. StagingCore
            # calls the real Core batch applicator; the PTB watcher chooses and
            # displays the next state. No test code sets a next state or adds facts.
            AnswerCore.propose(core, proposal.value)
            status = core.operations[operation]["status"]
            status["interpretation"] = proposal.model_dump(mode="json", by_alias=True)
            status["source"]["proposalSha256"] = canonical_hash(status["interpretation"])
            await delivery_finished(app)
            assert list(core.stages) == [operation]
            assert core.draft["draftData"]["affectedServices"] == ["Установка коронки"]
            assert core.draft["draftData"]["eventDate"] == {
                "date": "2026-10-15", "precision": "EXACT",
            }
            new_text = "\n".join(item.get("text", "") for item in sent[before:])
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] != "EVENT_DATE", (
                "the real dialogue asks again for the date supplied in the rich reply: "
                + new_text
            )

    asyncio.run(run())


def test_complete_factual_description_does_not_require_an_optional_clinic_file_before_summary():
    async def run():
        async with simplified_harness() as (app, core, _sent, dispatch):
            narrative = (
                "Учебный пример. 15.10.2026 посетитель впервые устно пожаловался на ожидание. "
                "Просит вернуть 5000 рублей. Клиника пока ничего не сделала. "
                "Ухудшения и госпитализации нет. Представителей, письменных требований, "
                "документов суда или органа и угроз обращения к ним нет."
            )
            await dispatch("quick:start")
            await dispatch("text:" + narrative)
            core.propose(incomingKind="COMPLAINT", situationAreas=["SERVICE"],
                         eventDate={"date": "2026-10-15", "precision": "EXACT"},
                         conflictStage="FIRST", clinicActions=["NOTHING_YET"],
                         healthSignals=["NO_KNOWN_INFORMATION"], safetyScreening={
                             "schemaVersion": SCREENING_VERSION,
                             **dict.fromkeys(SCREENING_FIELDS, "NO"), "moneyRequested": "YES",
                             "amount": {"amountKopecks": 500_000, "currency": "RUB"},
                         })
            await delivery_finished(app)
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SUMMARY", (
                "a missing optional caseMaterialsStatus still blocks an otherwise complete "
                "factual summary with a file-upload question"
            )

    asyncio.run(run())
