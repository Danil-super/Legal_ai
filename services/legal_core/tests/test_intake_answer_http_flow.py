"""Real Core→runner→Agent→private Core→PostgreSQL; only model output is synthetic."""

import asyncio
import json
import os
from contextlib import AsyncExitStack
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding
from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
from agent_orchestrator.main import ServiceDependencies, ServiceSettings, create_app as agent_app
from legal_core.analysis_job_worker import WorkerSettings
from legal_core.database import database_url, owner_database_url
from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_preparation_runner import IntakePreparationRunner
from legal_core.main import create_app as core_app
from legal_core.model_admission import ModelAdmission
from test_case_api import actor_headers, seed_admin

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)
KEY = "synthetic-answer-flow-key-12345678901234567890"
SUMMARY = "Пациент сообщил о сколе коронки после лечения."
TREATMENT = {"situationAreas": ["TREATMENT"], "affectedServices": ["установка коронки"]}
UNKNOWN_DATE = {"date": None, "precision": "UNKNOWN"}


def clarification(question_id):
    return TREATMENT | {
        "analysisClarificationRequests": [
            {
                "questionId": question_id,
                **({} if question_id == "RESPONSE_PURPOSE" else {"serviceSlot": 0}),
            }
        ]
    }


CASES = [
    ("INCOMING", {}, "Это письменная претензия", "FORMAL_DOCUMENT"),
    ("SITUATION", {}, "Лечение и обслуживание", ["TREATMENT", "SERVICE"]),
    ("SERVICES", {"situationAreas": ["TREATMENT"]}, "установка коронки", ["установка коронки"]),
    ("EVENT", {}, SUMMARY, SUMMARY),
    ("EVENT_DATE", {}, "Дата 15.10.2026", {"date": "2026-10-15", "precision": "EXACT"}),
    ("CHRONOLOGY", {}, "Это первое обращение", "FIRST"),
    ("CLINIC_ACTIONS", {}, "Пригласили на осмотр", ["INVITED_FOR_EXAMINATION"]),
    ("HEALTH", {}, "Не знаю", ["UNKNOWN"]),
    ("SAFETY", {}, "Нет, об ухудшении не сообщал", "NO"),
    ("ANALYSIS_CLARIFICATION", clarification("SERVICE_PAYMENT_BASIS"), "Услуга платная", "PAID"),
    (
        "ANALYSIS_CLARIFICATION",
        clarification("SERVICE_PROVIDED_DATE"),
        "Примерно 15.10.2026",
        {"date": "2026-10-15", "precision": "APPROXIMATE"},
    ),
    (
        "ANALYSIS_CLARIFICATION",
        clarification("SERVICE_PROVIDED_DATE"),
        "Не знаю дату",
        UNKNOWN_DATE,
    ),
    ("ANALYSIS_CLARIFICATION", clarification("RESPONSE_PURPOSE"), "Не знаю", "UNKNOWN"),
    (
        "SAFETY",
        {
            "safetyScreening": {
                "schemaVersion": "factual-safety-intake.v1",
                "healthDeteriorationReported": "NO",
                "hospitalizationReported": "NO",
                "representativeContact": "NO",
                "writtenRequirementsReceived": "NO",
                "authorityOrCourtDocumentReceived": "NO",
                "authorityReferralMentioned": "NO",
                "moneyRequested": "YES",
            }
        },
        "Требует вернуть 10 тысяч рублей",
        {"amountKopecks": 1000000, "currency": "RUB"},
    ),
]


def counts(clinic):
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            return tuple(
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE clinic_id=:clinic"),
                    {"clinic": clinic},
                )
                for table in ("cases", "case_facts", "case_reports", "analysis_jobs")
            )
    finally:
        engine.dispose()


async def run_flow(*, actor, clinic, state, data, answer, value, unresolved=False, cancel=False,
                   extra_facts=None, staging=False, expected_model_calls=1):
    engine = create_async_engine(database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    core = core_app(
        session_factory=sessions,
        managed_engine=engine,
        enable_draft_retention=False,
        enable_analysis_worker=False,
    )
    entered, release = asyncio.Event(), asyncio.Event()
    model_calls, agent_requests, core_requests = [], [], []
    core_transport = httpx.ASGITransport(app=core)

    async def send_core(request):
        core_requests.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
            )
        )
        response = await core_transport.handle_async_request(request)
        if (expected_model_calls == 0 and response.status_code == 200
                and request.url.path.startswith("/v1/intake-answer-preparations/")
                and request.url.path.endswith("/context")):
            # A literal has no model stage to block. Hold the actual private
            # response after Core has checked actor/lease/source instead.
            entered.set()
            await release.wait()
        return response

    async def model(request):
        assert expected_model_calls == 1, "a literal answer must not invoke the model"
        model_calls.append(json.loads(request.content))
        entered.set()
        await release.wait()
        interpretation = {
            "status": "UNRESOLVED" if unresolved else "PROPOSED",
            "value": None if unresolved else value,
            "sourceSpans": [] if unresolved else [{"start": 0, "end": len(answer)}],
            "reasonCode": "AMBIGUOUS" if unresolved else None,
        }
        if extra_facts:
            supplied = json.loads(model_calls[-1]["messages"][1]["content"])
            additions = []
            for field, (extra_value, literal) in extra_facts.items():
                field_name = field[0] if isinstance(field, tuple) else field
                target = next(item for item in supplied["allowedQuestions"]
                              if item["targetField"] == field_name and (
                                  not isinstance(field, tuple)
                                  or (item["clarificationRequest"]["questionId"] == field[1]
                                      and item["clarificationRequest"]["serviceSlot"] == field[2])
                              ))
                segment = next(item for item in supplied["retainedSources"]
                               if literal in item["text"])
                start = segment["text"].index(literal)
                additions.append({"question": target, "value": extra_value, "anchors": [{
                    "sourceId": segment["sourceId"], "sourceSha256": segment["sourceSha256"],
                    "start": start, "end": start + len(literal),
                }]})
            interpretation["factualProposal"] = {
                "schemaVersion": "ai-first-factual-proposal.v1", "candidates": additions,
            }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": json.dumps(interpretation)},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with AsyncExitStack() as lifetime:
        # ASGITransport deliberately does not trigger the real application lifespan.
        await lifetime.enter_async_context(core.router.lifespan_context(core))
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(send_core), base_url="http://core.test"
            ) as core_http,
            httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_http,
        ):
            researcher = HermesClient(
                HermesEndpoint(
                    "http://researcher:8642",
                    "synthetic-key",
                    model="existing-researcher",
                    timeout_seconds=65,
                ),
                client=model_http,
            )
            settings = ServiceSettings(
                KEY,
                "http://core.test",
                "http://researcher:8642",
                "r",
                "existing-researcher",
                "http://reviewer:8642",
                "v",
                "reviewer",
            )
            agent = agent_app(
                settings=settings,
                dependencies=ServiceDependencies(
                    legal_core=LegalCoreClient(
                        LegalCoreEndpoint("http://core.test", KEY), client=core_http
                    ),
                    reasoning=SimpleNamespace(),
                    answer_understanding=AnswerUnderstanding(researcher=researcher),
                ),
            )
            agent_transport = httpx.ASGITransport(app=agent)

            async def send_agent(request):
                agent_requests.append(
                    (request.url.path, json.loads(request.content), dict(request.headers))
                )
                return await agent_transport.handle_async_request(request)

            async with httpx.AsyncClient(transport=httpx.MockTransport(send_agent)) as agent_http:
                runner = IntakePreparationRunner(
                    sessions,
                    WorkerSettings("http://agent.test", KEY),
                    ModelAdmission(),
                    client=agent_http,
                )
                core.state.intake_preparation_runner = runner
                try:
                    created = await core_http.post(
                        "/v1/telegram-intake-drafts",
                        headers=actor_headers(actor, uuid4()),
                        json={"intakeSchemaVersion": "dental-case-intake.v2"},
                    )
                    assert created.status_code == 201, created.text
                    draft = created.json()
                    saved = await core_http.put(
                        f"/v1/telegram-intake-drafts/{draft['id']}",
                        headers=actor_headers(actor, uuid4()),
                        json={
                            "expectedRevision": draft["revision"],
                            "wizardState": state,
                            "draftData": {"intakeVersion": 2, "eventSummary": SUMMARY, **data},
                        },
                    )
                    assert saved.status_code == 200, saved.text
                    draft = saved.json()
                    question = derive_answer_question(draft["draftData"], state)
                    assert question is not None
                    core_requests.clear()
                    start_path = f"/v1/telegram-intake-drafts/{draft['id']}/answer-interpretations"
                    started = await asyncio.wait_for(
                        core_http.post(
                            start_path,
                            headers=actor_headers(actor),
                            json={"expectedRevision": draft["revision"], "answer": answer},
                        ),
                        timeout=2,
                    )
                    assert started.status_code == 202, started.text
                    pending = started.json()
                    assert pending["state"] == "PENDING"
                    await asyncio.wait_for(entered.wait(), timeout=2)
                    operation = f"/v1/intake-answer-preparations/{pending['operationId']}"
                    actual = await core_http.get(
                        f"/v1/telegram-intake-drafts/{draft['id']}", headers=actor_headers(actor)
                    )
                    assert actual.json() == draft and counts(clinic) == (0, 0, 0, 0)
                    replay = await core_http.post(
                        start_path,
                        headers=actor_headers(actor),
                        json={"expectedRevision": draft["revision"], "answer": answer},
                    )
                    assert replay.status_code == 202 and replay.json() == pending
                    # HTTP intent returns before either the model or the literal's
                    # private-context delivery completes; both preserve draft data.
                    assert len(model_calls) == expected_model_calls
                    assert len(agent_requests) == 1 and not release.is_set()
                    metadata = agent_requests[0][1]
                    assert set(metadata) == {
                        "draftId",
                        "leaseToken",
                        "expectedRevision",
                        "expectedInputSha256",
                    }
                    assert answer not in json.dumps(metadata, ensure_ascii=False)
                    if expected_model_calls:
                        user = json.loads(model_calls[0]["messages"][1]["content"])
                        assert user["answer"] == answer and user["question"] == question.model_dump(
                            mode="json", by_alias=True
                        )
                        assert model_calls[0]["model"] == "existing-researcher"
                        model_source = model_calls[0]["messages"][1]["content"]
                        assert metadata["leaseToken"] not in model_source
                        assert draft["id"] not in model_source
                    assert any(path == operation + "/context" for _, path, _ in core_requests)
                    if cancel:
                        cancelled = await asyncio.wait_for(
                            core_http.delete(operation, headers=actor_headers(actor)), timeout=2
                        )
                        assert cancelled.status_code == 200
                        assert cancelled.json()["state"] == "MANUAL_CONTINUATION"
                    release.set()
                    finished = await asyncio.wait_for(
                        asyncio.gather(*tuple(runner._tasks), return_exceptions=True), timeout=5,
                    )
                    assert all(item is None or (cancel and isinstance(item, asyncio.CancelledError))
                               for item in finished)
                    completed = await core_http.get(operation, headers=actor_headers(actor))
                    assert completed.status_code == 200, completed.text
                    status = completed.json()
                    actual = await core_http.get(
                        f"/v1/telegram-intake-drafts/{draft['id']}", headers=actor_headers(actor)
                    )
                    assert actual.json() == draft and counts(clinic) == (0, 0, 0, 0)
                    assert all(method != "PUT" for method, _, _ in core_requests)
                    if cancel:
                        assert status["state"] == "MANUAL_CONTINUATION"
                        return
                    assert status["state"] == ("UNRESOLVED" if unresolved else "PROPOSED"), status
                    source = status["source"]
                    consent = {
                        "expectedRevision": source["draftRevision"],
                        "expectedInputSha256": source["inputSha256"],
                        "expectedProposalSha256": source["proposalSha256"],
                    }
                    if unresolved:
                        assert status["interpretation"]["value"] is None
                        refused = await core_http.post(
                            operation + "/acceptance", headers=actor_headers(actor), json=consent
                        )
                        assert refused.status_code == 409 and counts(clinic) == (0, 0, 0, 0)
                        return
                    assert status["question"] == question.model_dump(mode="json", by_alias=True)
                    assert status["interpretation"]["value"] == value
                    suffix = "/draft-staging" if staging else "/acceptance"
                    accepted = await core_http.post(operation + suffix,
                                                    headers=actor_headers(actor), json=consent)
                    assert accepted.status_code == 200, accepted.text
                    receipt = accepted.json()
                    assert receipt["stagedRevision" if staging else "acceptedRevision"] == (
                        draft["revision"] + 1
                    )
                    result = receipt["draft"]["draftData"]
                    if state == "ANALYSIS_CLARIFICATION":
                        assert (
                            result["analysisClarificationAnswers"]["answers"][0]["value"] == value
                        )
                    elif state == "SAFETY":
                        assert result["safetyScreening"][question.screening_field] == value
                    else:
                        assert result[question.target_field] == value
                    for field, (extra_value, _) in (extra_facts or {}).items():
                        if isinstance(field, tuple):
                            assert next(item["value"] for item in result[field[0]]["answers"]
                                        if item["questionId"] == field[1]
                                        and item["serviceSlot"] == field[2]) == extra_value
                        else:
                            assert result[field] == extra_value
                    replayed = await core_http.post(operation + suffix,
                                                    headers=actor_headers(actor), json=consent)
                    assert replayed.status_code == 200 and replayed.json() == receipt
                    assert len(model_calls) == expected_model_calls
                    assert len(agent_requests) == 1
                    assert counts(clinic) == (0, 0, 0, 0)
                finally:
                    release.set()
                    await runner.close()
                    core.state.intake_preparation_runner = None


@pytest.mark.parametrize("state,data,answer,value", CASES)
def test_all_factual_question_families_use_real_private_http_and_only_mutate_after_consent(
    monkeypatch,
    state,
    data,
    answer,
    value,
):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", KEY)
    actor = 38_800_000_000 + uuid4().int % 100_000_000
    clinic, _ = seed_admin(actor)
    asyncio.run(
        run_flow(actor=actor, clinic=clinic, state=state, data=data, answer=answer, value=value)
    )


@pytest.mark.parametrize("outcome", ["unresolved", "cancel"])
def test_ambiguous_and_cancelled_answers_never_mutate_confirmed_data_or_create_case(
    monkeypatch,
    outcome,
):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", KEY)
    actor = 39_800_000_000 + uuid4().int % 100_000_000
    clinic, _ = seed_admin(actor)
    asyncio.run(
        run_flow(
            actor=actor,
            clinic=clinic,
            state="INCOMING",
            data={},
            answer="Как писал выше",
            value="FORMAL_DOCUMENT",
            unresolved=outcome == "unresolved",
            cancel=outcome == "cancel",
        )
    )


def test_rich_reply_uses_real_core_agent_http_and_stages_all_facts_with_one_model_call(monkeypatch):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", KEY)
    monkeypatch.setenv("AI_FIRST_FACTUAL_INTAKE_ENABLED", "true")
    actor = 40_800_000_000 + uuid4().int % 100_000_000
    clinic, _ = seed_admin(actor)
    asyncio.run(run_flow(
        actor=actor, clinic=clinic, state="SERVICES", data={"situationAreas": ["TREATMENT"]},
        answer="Установка коронки выполнена 10.10.2026, платно. "
               "Обращение 15.10.2026. Пригласили на осмотр.",
        value=["Установка коронки"], staging=True, extra_facts={
            "eventDate": ({"date": "2026-10-15", "precision": "EXACT"}, "15.10.2026"),
            "clinicActions": (["INVITED_FOR_EXAMINATION"], "Пригласили на осмотр"),
            ("analysisClarificationAnswers", "SERVICE_PROVIDED_DATE", 0): (
                {"date": "2026-10-10", "precision": "EXACT"},
                "Установка коронки выполнена 10.10.2026, платно.",
            ),
            ("analysisClarificationAnswers", "SERVICE_PAYMENT_BASIS", 0): (
                "PAID", "Установка коронки выполнена 10.10.2026, платно.",
            ),
        },
    ))


@pytest.mark.parametrize("state,data,answer,value", [
    ("EVENT_DATE", {}, "15.10.2026", {"date": "2026-10-15", "precision": "EXACT"}),
    ("EVENT_DATE", {}, "2026-10-15", {"date": "2026-10-15", "precision": "EXACT"}),
    ("SAFETY", {}, "Нет", "NO"),
    ("ANALYSIS_CLARIFICATION", clarification("SERVICE_PAYMENT_BASIS"), "платно", "PAID"),
    ("ANALYSIS_CLARIFICATION", clarification("SERVICE_PROVIDED_DATE"), "15.10.2026",
     {"date": "2026-10-15", "precision": "EXACT"}),
])
def test_literal_answers_use_real_private_context_and_require_consent_without_a_model(
    monkeypatch, state, data, answer, value,
):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", KEY)
    actor = 41_800_000_000 + uuid4().int % 100_000_000
    clinic, _ = seed_admin(actor)
    asyncio.run(run_flow(actor=actor, clinic=clinic, state=state, data=data,
                         answer=answer, value=value, expected_model_calls=0))
