# ruff: noqa: RUF001
"""Fast durable analysis submission and restart-safe Telegram result delivery."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import httpx2
from legal_core.analysis_clarifications import (
    ClarificationQuestionId,
    ClarificationRequest,
    ClarificationRequestBatch,
    validate_request_scope,
)
from legal_core.analysis_contracts import (
    PreparationJobResponse,
    PreparationResult,
    ReportReadyResult,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.clarification_intent import (
    encode_clarification_callback,
    encode_preparation_callback,
)
from telegram_gateway.ui import back_keyboard

logger = logging.getLogger(__name__)
JOBS_CLIENT_KEY = "analysis_jobs_client"
TERMINAL_STATES = {"SUCCEEDED", "FAILED"}


@asynccontextmanager
async def _message_lock(
    context: ContextTypes.DEFAULT_TYPE, actor: int, message_id: int
) -> AsyncIterator[None]:
    """Prevent a late QUEUED edit from overwriting an already delivered terminal result.

    Entries exist only while tasks use them; no case data or unbounded lock cache is retained.
    """
    locks = context.bot_data.setdefault("analysis_message_locks", {})
    key = (actor, message_id)
    entry = locks.setdefault(key, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] == 0:
            locks.pop(key, None)


@dataclass(frozen=True)
class JobSnapshot:
    job_id: UUID
    case_id: UUID
    state: str
    error_code: str | None
    result: dict[str, Any] | None
    result_version: int = 1

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> JobSnapshot:
        version = payload.get("resultVersion", 1)
        if type(version) is not int or version not in {1, 2}:
            raise ValueError("unsupported analysis result version")
        if version == 2:
            prepared = PreparationJobResponse.model_validate(payload)
            return cls(
                prepared.job_id, prepared.case_id, prepared.state, prepared.error_code,
                prepared.result.model_dump(mode="json", by_alias=True)
                if prepared.result is not None else None,
                result_version=2,
            )
        try:
            job_id, case_id = UUID(str(payload["jobId"])), UUID(str(payload["caseId"]))
            state, result = payload["state"], payload.get("result")
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("invalid analysis job") from exc
        if not isinstance(state, str) or state not in {"QUEUED", "RUNNING", *TERMINAL_STATES}:
            raise ValueError("invalid analysis state")
        if state == "SUCCEEDED" and not isinstance(result, dict):
            raise ValueError("completed analysis has no canonical result")
        if state != "SUCCEEDED" and result is not None:
            raise ValueError("nonterminal analysis includes a result")
        if isinstance(result, dict) and "resultVersion" in result:
            raise ValueError("versioned result requires a versioned job envelope")
        code = payload.get("errorCode")
        if code is not None and (not isinstance(code, str) or len(code) > 80):
            raise ValueError("invalid analysis error")
        return cls(job_id, case_id, state, code, result)


class AnalysisJobsClient:
    def __init__(self, http: httpx2.AsyncClient, internal_key: str):
        self.http = http
        self.internal_key = internal_key

    async def request(
        self,
        method: str,
        path: str,
        *,
        actor: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = (
            {"X-Telegram-User-Id": str(actor)}
            if actor is not None
            else {"X-Agent-Internal-Key": self.internal_key}
        )
        try:
            # Total timeout also bounds a peer that trickles bytes indefinitely.
            async with asyncio.timeout(6):
                response = await self.http.request(method, path, headers=headers, json=payload)
        except (httpx2.HTTPError, TimeoutError) as exc:
            raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "Core unavailable") from exc
        if response.status_code == 204:
            return {}
        try:
            body = response.json()
        except ValueError as exc:
            raise LegalCoreApiError(502, "INVALID_JOB_RESPONSE", "Invalid job response") from exc
        if response.status_code >= 400:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            raise LegalCoreApiError(
                response.status_code,
                code if isinstance(code, str) else "ANALYSIS_JOB_UNAVAILABLE",
                "Job request rejected",
            )
        if not isinstance(body, dict):
            raise LegalCoreApiError(502, "INVALID_JOB_RESPONSE", "Invalid job response")
        return body

    async def enqueue(self, case_id: UUID, actor: int, message_id: int) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/v1/cases/{case_id}/analysis-jobs",
            actor=actor,
            payload={"messageId": message_id},
        )

    async def status(self, job_id: UUID, actor: int) -> dict[str, Any]:
        return await self.request("GET", f"/v1/analysis-jobs/{job_id}", actor=actor)

    async def notifications(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/internal/analysis-job-notifications")

    async def ack(self, job_id: UUID, actor: int) -> None:
        await self.request("POST", f"/v1/analysis-jobs/{job_id}/notification-ack", actor=actor)


def _client(context: ContextTypes.DEFAULT_TYPE) -> AnalysisJobsClient:
    client = context.bot_data.get(JOBS_CLIENT_KEY)
    if client is None:
        raise LegalCoreApiError(503, "ANALYSIS_SERVICE_UNAVAILABLE", "Jobs not initialized")
    return cast(AnalysisJobsClient, client)


def _report_clarification_callback(job: JobSnapshot) -> str | None:
    from telegram_gateway.analysis_runtime import factual_clarification_code

    result = job.result
    if result is None:
        return None
    response = result.get("report")
    canonical = response.get("reportJson") if isinstance(response, dict) else None
    if not isinstance(canonical, dict):
        return None
    summary, facts = canonical.get("summary"), canonical.get("facts")
    availability = summary.get("analysisAvailability") if isinstance(summary, dict) else None
    if not isinstance(availability, dict) or not isinstance(facts, dict):
        return None
    try:
        requests = ClarificationRequestBatch.model_validate(
            {"requests": canonical.get("clarificationRequests", [])}
        ).requests
        validate_request_scope(requests, facts)
    except ValueError:
        requests = []
    pending = bool(requests) and availability.get("status") == "BLOCKED"
    if factual_clarification_code(result) is None and not pending:
        return None
    assert isinstance(response, dict)
    report_id = UUID(str(response.get("id")))
    case = canonical.get("case")
    if (UUID(str(response.get("caseId"))) != job.case_id
            or UUID(str(canonical.get("reportId"))) != report_id
            or not isinstance(case, dict) or UUID(str(case.get("id"))) != job.case_id):
        raise ValueError("clarification report identity mismatch")
    return encode_clarification_callback(job.case_id, report_id)


def _ready_for_presentation(result: ReportReadyResult) -> dict[str, Any]:
    """Adapt only a validated READY result for the existing report presentation."""
    return {
        "analysisAllowed": True, "riskLevel": result.report.report_json["risk"]["level"],
        "escalationRequired": False, "escalationId": None,
        "report": result.report.model_dump(mode="json", by_alias=True),
    }


def _preparation_question(request: ClarificationRequest) -> str:
    """Use typed identities only: this envelope has no trusted service titles."""
    if request.question_id == ClarificationQuestionId.RESPONSE_PURPOSE:
        return "Что клинике нужно сделать в связи с обращением?"
    assert request.service_slot is not None
    service = f"услуга №{request.service_slot + 1}"
    if request.question_id == ClarificationQuestionId.SERVICE_PAYMENT_BASIS:
        return f"Как оплачивалась {service}?"
    return f"Когда была оказана {service}?"


def _preparation_message(job: JobSnapshot) -> tuple[str, InlineKeyboardMarkup]:
    result = PreparationResult.model_validate(job.result).root
    if result.source.case_id != job.case_id:
        raise ValueError("preparation result case mismatch")
    if isinstance(result, ReportReadyResult):
        return job_message(JobSnapshot(
            job.job_id, job.case_id, job.state, job.error_code, _ready_for_presentation(result),
        ))
    rows: list[list[InlineKeyboardButton]] = []
    if result.status == "NEEDS_FACTS":
        questions = [_preparation_question(request) for request in result.clarification_requests]
        text = ("Уточните, пожалуйста:\n" + "\n".join(questions) if questions else
                "Для подготовки рекомендаций нужно уточнить факты.")
    elif result.status == "LIMITED":
        text = (
            "Пока недостаточно подтверждённых данных или применимой нормативной базы. "
            "Юридические рекомендации не сформированы."
        )
    else:
        text = "Кейс направлен юристу. Дальнейшие рекомендации подготовим после его проверки."
        rows.insert(0, [InlineKeyboardButton(
            "⚖️ Карточка юриста", callback_data=f"case:escalation:{result.escalation_id}",
        )])
    if result.status in {"NEEDS_FACTS", "LIMITED"}:
        rows.insert(0, [InlineKeyboardButton(
            "Ответить" if result.status == "NEEDS_FACTS" else "Изменить сведения",
            callback_data=encode_preparation_callback(job.case_id, result.source.operation_id),
        )])
    rows.extend(list(row) for row in back_keyboard().inline_keyboard)
    return text, InlineKeyboardMarkup(rows)


def job_message(job: JobSnapshot) -> tuple[str, InlineKeyboardMarkup]:
    from telegram_gateway.analysis_runtime import (
        analysis_error_message,
        escalation_id_from_analysis,
        telegram_analysis_messages,
    )

    if job.result_version == 2 and job.state == "SUCCEEDED":
        return _preparation_message(job)

    rows = [] if job.state in TERMINAL_STATES else [
        [InlineKeyboardButton("🔄 Проверить статус", callback_data=f"analysis:status:{job.job_id}")]
    ]
    if job.state == "QUEUED":
        text = "⏳ Анализ поставлен в очередь. Можно пользоваться остальными кнопками бота."
    elif job.state == "RUNNING":
        text = "⚖️ Анализ выполняется: проверяем факты и правовую основу. Остальные меню доступны."
    elif job.state == "FAILED":
        text = f"⚠️ {analysis_error_message(job.error_code or '')}\nКейс сохранён."
        from telegram_gateway.factual_safety_intake import clarification_question_for_code

        needs_clarification = clarification_question_for_code(job.error_code or "") is not None \
            or job.error_code in {"INSUFFICIENT_FACTS", "ANALYSIS_DATE_UNCERTAIN"}
        action = "clarify" if needs_clarification else "analyze"
        label = "Уточнить факты" if needs_clarification else "Повторить анализ"
        rows.append([InlineKeyboardButton(label, callback_data=f"case:{action}:{job.case_id}")])
    else:
        if job.result is None:
            raise ValueError("missing result")
        text = telegram_analysis_messages(job.result)[0]
        rows.insert(
            0, [InlineKeyboardButton("📋 Результат", callback_data=f"analysis:result:{job.job_id}")]
        )
        rows.insert(
            1,
            [InlineKeyboardButton("📄 PDF результата", callback_data=f"analysis:pdf:{job.job_id}")],
        )
        escalation_id = escalation_id_from_analysis(job.result)
        clarification_callback = (
            _report_clarification_callback(job)
            if job.result.get("analysisAllowed") is False and escalation_id is None else None
        )
        if clarification_callback is not None:
            rows.append([InlineKeyboardButton(
                "Уточнить факты", callback_data=clarification_callback,
            )])
        if escalation_id is not None:
            rows.insert(
                0,
                [
                    InlineKeyboardButton(
                        "⚖️ Карточка юриста", callback_data=f"case:escalation:{escalation_id}"
                    )
                ],
            )
    rows.extend(list(row) for row in back_keyboard().inline_keyboard)
    return text, InlineKeyboardMarkup(rows)


async def _edit_status(
    context: ContextTypes.DEFAULT_TYPE, actor: int, message_id: int, job: JobSnapshot
) -> None:
    text, keyboard = job_message(job)
    try:
        await context.bot.edit_message_text(
            chat_id=actor, message_id=message_id, text=text, reply_markup=keyboard
        )
    except BadRequest as exc:
        if str(exc).lower() != "message is not modified" and not str(exc).lower().startswith(
            "message is not modified:"
        ):
            raise


async def enqueue_analysis(
    update: Update, context: ContextTypes.DEFAULT_TYPE, case_id: UUID
) -> None:
    actor = gateway_bot._actor_id(update)
    if actor is None or actor <= 0:
        raise ValueError("invalid analysis actor")
    # Always address the requesting user's private chat, never a callback's arbitrary group chat.
    placeholder = await context.bot.send_message(
        chat_id=actor,
        text="⏳ Сохраняю запрос анализа…",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Проверить запрос", callback_data=f"case:analyze:{case_id}")],
                *back_keyboard().inline_keyboard,
            ]
        ),
    )
    try:
        client = _client(context)
        async with _message_lock(context, actor, placeholder.message_id):
            job = JobSnapshot.parse(await client.enqueue(case_id, actor, placeholder.message_id))
            if job.case_id != case_id:
                raise ValueError("analysis response case mismatch")
            await _edit_status(context, actor, placeholder.message_id, job)
        # Do not ACK here: duplicate submission may refer to a different persisted placeholder.
        # The notifier will idempotently update that original message before acknowledging it.
    except (LegalCoreApiError, ValueError) as exc:
        from telegram_gateway.analysis_runtime import analysis_error_message

        logger.warning("analysis enqueue rejected: %s", type(exc).__name__)
        text = (
            analysis_error_message(exc.code)
            if isinstance(exc, LegalCoreApiError)
            else "Ответ сервера не прошёл проверку."
        )
        await context.bot.edit_message_text(
            chat_id=actor,
            message_id=placeholder.message_id,
            text=f"⚠️ {text} Повторное нажатие безопасно.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Проверить запрос", callback_data=f"case:analyze:{case_id}"
                        )
                    ],
                    *back_keyboard().inline_keyboard,
                ]
            ),
        )


async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, actor = update.callback_query, gateway_bot._actor_id(update)
    if query is None or actor is None or not isinstance(query.data, str):
        raise ApplicationHandlerStop
    await query.answer()
    try:
        _, action, raw_id = query.data.split(":")
        job_id = UUID(raw_id)
        if action == "pdf":
            from telegram_gateway.report_pdf_delivery import queue_report_pdf

            async def resolve_report() -> UUID:
                current = JobSnapshot.parse(await _client(context).status(job_id, actor))
                if (
                    current.job_id != job_id
                    or current.state != "SUCCEEDED"
                    or current.result is None
                ):
                    raise ValueError("analysis PDF unavailable")
                if current.result_version == 2 and current.result.get("status") != "READY":
                    raise ValueError("preparation has no final PDF")
                report = current.result.get("report")
                if (
                    not isinstance(report, dict)
                    or UUID(str(report.get("caseId"))) != current.case_id
                ):
                    raise ValueError("analysis report case mismatch")
                report_id = UUID(str(report.get("id")))
                canonical = report.get("reportJson")
                if (
                    not isinstance(canonical, dict)
                    or UUID(str(canonical.get("reportId"))) != report_id
                ):
                    raise ValueError("canonical report identity mismatch")
                case = canonical.get("case")
                if not isinstance(case, dict) or UUID(str(case.get("id"))) != current.case_id:
                    raise ValueError("canonical report case mismatch")
                return report_id

            await queue_report_pdf(
                context,
                actor_id=actor,
                resolve_report=resolve_report,
                return_callback=f"analysis:status:{job_id}",
            )
            raise ApplicationHandlerStop
        job = JobSnapshot.parse(await _client(context).status(job_id, actor))
        if job.job_id != job_id:
            raise ValueError("analysis response job mismatch")
        if action == "result" and job.result is not None:
            from telegram_gateway.analysis_runtime import telegram_analysis_messages

            if job.result_version == 2:
                prepared_result = PreparationResult.model_validate(job.result).root
                messages = telegram_analysis_messages(_ready_for_presentation(prepared_result)) \
                    if isinstance(prepared_result, ReportReadyResult) else [job_message(job)[0]]
            else:
                messages = telegram_analysis_messages(job.result)
            for text in messages:
                await context.bot.send_message(
                    chat_id=actor, text=text, reply_markup=job_message(job)[1]
                )
        else:
            message = query.message
            if message is not None and message.chat.id == actor:
                async with _message_lock(context, actor, message.message_id):
                    # Re-read inside the edit lock: a notifier may have completed meanwhile.
                    fresh = JobSnapshot.parse(await _client(context).status(job_id, actor))
                    if fresh.job_id != job_id:
                        raise ValueError("analysis response job mismatch")
                    await _edit_status(context, actor, message.message_id, fresh)
            else:
                text, keyboard = job_message(job)
                await context.bot.send_message(chat_id=actor, text=text, reply_markup=keyboard)
    except (LegalCoreApiError, ValueError) as exc:
        logger.warning("analysis status unavailable: %s", type(exc).__name__)
        await context.bot.send_message(
            chat_id=actor,
            text="⚠️ Результат недоступен для этого аккаунта или сервер временно недоступен.",
            reply_markup=back_keyboard(),
        )
    raise ApplicationHandlerStop


async def notify_completed_analyses(context: ContextTypes.DEFAULT_TYPE) -> None:
    if time.monotonic() < context.bot_data.get("analysis_notification_retry_at", 0):
        return
    client = _client(context)
    try:
        payload = await client.notifications()
        items = payload.get("items")
        if not isinstance(items, list) or len(items) > 20:
            raise ValueError("invalid notification batch")
    except (LegalCoreApiError, ValueError) as exc:
        logger.warning("analysis notification listing failed: %s", type(exc).__name__)
        return
    semaphore = asyncio.Semaphore(4)

    async def deliver(item: object) -> None:
        async with semaphore:
            try:
                if not isinstance(item, dict):
                    raise ValueError("invalid notification")
                job_id = UUID(str(item.get("jobId")))
                actor, message_id = item.get("telegramUserId"), item.get("messageId")
                if (
                    type(actor) is not int
                    or actor <= 0
                    or type(message_id) is not int
                    or message_id <= 0
                ):
                    raise ValueError("invalid notification destination")
                async with _message_lock(context, actor, message_id):
                    job = JobSnapshot.parse(await client.status(job_id, actor))
                    if job.job_id != job_id or job.state not in TERMINAL_STATES:
                        raise ValueError("notification result mismatch")
                    try:
                        await _edit_status(context, actor, message_id, job)
                    except (BadRequest, Forbidden) as exc:
                        if str(exc).lower() not in {
                            "message to edit not found",
                            "message can't be edited",
                            "forbidden: bot was blocked by the user",
                            "forbidden: user is deactivated",
                            "bot was blocked by the user",
                            "user is deactivated",
                        }:
                            raise
                        # Deleted placeholder: durable result is available via the original CTA.
                        logger.info("analysis notification placeholder unavailable")
                    await client.ack(job_id, actor)
            except RetryAfter as exc:
                seconds = (
                    exc.retry_after.total_seconds()
                    if isinstance(exc.retry_after, timedelta)
                    else exc.retry_after
                )
                context.bot_data["analysis_notification_retry_at"] = max(
                    context.bot_data.get("analysis_notification_retry_at", 0),
                    time.monotonic() + seconds,
                )
                logger.warning("analysis notifications rate limited; retry deferred")
            except (LegalCoreApiError, ValueError, TelegramError) as exc:
                logger.warning("analysis notification deferred: %s", type(exc).__name__)

    try:
        async with asyncio.timeout(25):
            await asyncio.gather(*(deliver(item) for item in items))
    except TimeoutError:
        logger.warning("analysis notification batch reached time budget")


def install_analysis_jobs(application: gateway_bot.TelegramApplication, internal_key: str) -> None:
    previous_init, previous_shutdown = application.post_init, application.post_shutdown

    async def initialize(app: gateway_bot.TelegramApplication) -> None:
        if previous_init is not None:
            await previous_init(app)
        app.bot_data[JOBS_CLIENT_KEY] = AnalysisJobsClient(
            httpx2.AsyncClient(
                base_url=gateway_bot.load_legal_core_url(),
                timeout=5,
                follow_redirects=False,
                trust_env=False,
                limits=httpx2.Limits(max_connections=8, max_keepalive_connections=4),
            ),
            internal_key,
        )
        if app.job_queue is None:
            raise RuntimeError("durable analysis notifications require JobQueue")
        # PTB JobQueue jobs are independent of the sequential conversation update processor.
        # https://docs.python-telegram-bot.org/en/stable/telegram.ext.jobqueue.html
        app.job_queue.run_repeating(
            notify_completed_analyses,
            interval=5,
            first=1,
            name="durable-analysis-notifications",
            job_kwargs={"max_instances": 1, "coalesce": True},
        )

    async def shutdown(app: gateway_bot.TelegramApplication) -> None:
        client = app.bot_data.pop(JOBS_CLIENT_KEY, None)
        if client is not None:
            await client.http.aclose()
        if previous_shutdown is not None:
            await previous_shutdown(app)

    application.post_init, application.post_shutdown = initialize, shutdown
    application.add_handler(
        CallbackQueryHandler(
            status_callback, pattern=r"^analysis:(status|result|pdf):[0-9a-f-]{36}$"
        ),
        group=-1,
    )
