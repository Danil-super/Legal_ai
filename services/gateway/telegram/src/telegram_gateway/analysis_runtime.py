# ruff: noqa: RUF001
"""Optional Telegram runtime extension for evidence-gated legal analysis."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx2
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes

from telegram_gateway import bot as gateway_bot

logger = logging.getLogger(__name__)
ANALYSIS_CALLBACK_PREFIX = "case:analyze:"
ANALYSIS_TIMEOUT_SECONDS = 90.0
_PATCHED = False


class AgentOrchestratorApiError(RuntimeError):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True, slots=True)
class AnalysisSettings:
    base_url: str
    internal_key: str


def load_analysis_settings(
    environment: Mapping[str, str] | None = None,
) -> AnalysisSettings | None:
    source = os.environ if environment is None else environment
    base_url = source.get("AGENT_ORCHESTRATOR_URL", "").strip().rstrip("/")
    internal_key = source.get("AGENT_INTERNAL_KEY", "").strip()
    if not base_url and not internal_key:
        return None
    if not base_url or len(internal_key) < 32:
        raise RuntimeError("analysis runtime is partially configured")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("AGENT_ORCHESTRATOR_URL is malformed")
    return AnalysisSettings(base_url=base_url, internal_key=internal_key)


def analysis_keyboard(case_id: UUID) -> InlineKeyboardMarkup:
    callback = f"{ANALYSIS_CALLBACK_PREFIX}{case_id}"
    if len(callback.encode()) > 64:
        raise ValueError("Telegram analysis callback is too long")
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⚖️ Запустить юридический анализ", callback_data=callback)]]
    )


def _bounded_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def telegram_analysis_summary(payload: dict[str, Any]) -> str:
    """Render only the server-verified analysis fields returned by Legal Core."""

    if payload.get("analysisAllowed") is not True:
        risk = _bounded_text(payload.get("riskLevel"), limit=24) or "UNAVAILABLE"
        return (
            "⚖️ Юридический анализ не прошёл проверку доказательств.\n"
            f"Риск: {risk}\n\n"
            "Бот не будет додумывать вывод. Проверьте недостающие факты или передайте кейс юристу."
        )

    report = payload.get("report")
    if not isinstance(report, dict):
        raise ValueError("analysis response has no report")
    report_json = report.get("reportJson")
    if not isinstance(report_json, dict):
        raise ValueError("analysis response has no canonical report")

    case = report_json.get("case")
    risk = report_json.get("risk")
    recommendations = report_json.get("recommendations")
    legal_basis = report_json.get("legalBasis")
    draft = report_json.get("draftResponse")
    if not all(
        isinstance(value, dict)
        for value in (case, risk, recommendations, legal_basis, draft)
    ):
        raise ValueError("canonical analysis report is incomplete")

    public_number = _bounded_text(case.get("publicNumber"), limit=64)
    risk_level = _bounded_text(risk.get("level"), limit=24)
    reason_codes = risk.get("reasonCodes")
    action_items = recommendations.get("items")
    sources = legal_basis.get("sources")
    draft_status = _bounded_text(draft.get("status"), limit=32)
    draft_reason = _bounded_text(draft.get("reasonCode"), limit=80)
    if (
        public_number is None
        or risk_level is None
        or not isinstance(reason_codes, list)
        or not isinstance(action_items, list)
        or not isinstance(sources, list)
        or draft_status is None
    ):
        raise ValueError("canonical analysis report has invalid fields")

    lines = [
        f"⚖️ АНАЛИЗ {public_number}",
        f"Риск: {risk_level}",
    ]
    if risk.get("escalationRequired") is True:
        lines.append("🔴 Требуется передача ответственному юристу.")

    safe_reasons = [
        item[:80] for item in reason_codes[:8] if isinstance(item, str) and item.strip()
    ]
    if safe_reasons:
        lines.extend(["", "Почему:", *(f"• {item}" for item in safe_reasons)])

    safe_actions = [
        item[:500] for item in action_items[:8] if isinstance(item, str) and item.strip()
    ]
    if safe_actions:
        lines.extend(["", "Что сделать:", *(f"• {item}" for item in safe_actions)])

    source_lines: list[str] = []
    for source in sources[:6]:
        if not isinstance(source, dict):
            continue
        title = _bounded_text(source.get("documentTitle"), limit=180)
        path = _bounded_text(source.get("structuralPath"), limit=100)
        url = _bounded_text(source.get("sourceUrl"), limit=500)
        if title and path and url:
            source_lines.append(f"• {title}, {path}\n  {url}")
    if source_lines:
        lines.extend(["", "Правовая основа:", *source_lines])

    lines.extend(["", f"Черновик пациенту: {draft_status}"])
    if draft_reason:
        lines.append(f"Причина: {draft_reason}")
    lines.append("Автоматическая отправка пациенту отключена.")

    rendered = "\n".join(lines)
    if len(rendered) > 4_000:
        rendered = rendered[:3_900] + "\n…"
    return rendered


async def _call_analysis(
    settings: AnalysisSettings,
    *,
    case_id: UUID,
    telegram_user_id: int,
) -> dict[str, Any]:
    client = httpx2.AsyncClient(
        base_url=settings.base_url,
        timeout=ANALYSIS_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        try:
            response = await client.post(
                f"/v1/cases/{case_id}/analyze",
                headers={
                    "X-Agent-Internal-Key": settings.internal_key,
                    "X-Telegram-User-Id": str(telegram_user_id),
                    "Idempotency-Key": str(uuid4()),
                },
            )
        except httpx2.HTTPError as exc:
            raise AgentOrchestratorApiError(503, "ANALYSIS_SERVICE_UNAVAILABLE") from exc
        if response.status_code >= 400:
            code = "ANALYSIS_FAILED"
            try:
                body = response.json()
                detail = body.get("detail") if isinstance(body, dict) else None
                if isinstance(detail, dict) and isinstance(detail.get("code"), str):
                    code = detail["code"]
            except ValueError:
                pass
            raise AgentOrchestratorApiError(response.status_code, code)
        try:
            body = response.json()
        except ValueError as exc:
            raise AgentOrchestratorApiError(502, "INVALID_ANALYSIS_RESPONSE") from exc
        if not isinstance(body, dict):
            raise AgentOrchestratorApiError(502, "INVALID_ANALYSIS_RESPONSE")
        return body
    finally:
        await client.aclose()


async def analyze_case_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    settings: AnalysisSettings,
) -> None:
    query = update.callback_query
    actor = update.effective_user
    if query is None or actor is None or not isinstance(query.data, str):
        raise ApplicationHandlerStop
    await query.answer()
    try:
        case_id = UUID(query.data.removeprefix(ANALYSIS_CALLBACK_PREFIX))
        await gateway_bot._reply(
            update,
            "⚖️ Проверяю факты, применимую редакцию права и уровень риска…",
        )
        payload = await _call_analysis(settings, case_id=case_id, telegram_user_id=actor.id)
        await gateway_bot._reply(update, telegram_analysis_summary(payload))
    except (ValueError, AgentOrchestratorApiError) as exc:
        logger.warning("case analysis failed: %s", type(exc).__name__)
        if isinstance(exc, AgentOrchestratorApiError):
            messages = {
                "INSUFFICIENT_FACTS": "В кейсе не хватает обязательных фактов.",
                "LEGAL_EVIDENCE_UNAVAILABLE": (
                    "Для этого кейса пока не хватает одобренной правовой базы."
                ),
                "RISK_POLICY_NOT_READY": "Политика риска пока не активирована.",
                "ANALYSIS_CONTEXT_STALE": (
                    "Кейс изменился во время анализа. Запустите проверку ещё раз."
                ),
                "ANALYSIS_PROVIDER_UNAVAILABLE": "ИИ-провайдер временно недоступен.",
            }
            detail = messages.get(exc.code, "Не удалось безопасно завершить анализ.")
        else:
            detail = "Ответ анализа не прошёл внутреннюю проверку."
        await gateway_bot._reply(update, f"⚠️ {detail}")
    raise ApplicationHandlerStop


def _install_report_analysis_button(settings: AnalysisSettings) -> None:
    global _PATCHED
    if _PATCHED:
        return
    original = gateway_bot._send_workflow_report

    async def wrapped_send_workflow_report(
        update: Update,
        client: gateway_bot.LegalCoreClient,
        workflow: dict[str, Any],
        actor_id: int,
    ) -> None:
        await original(update, client, workflow, actor_id)
        case = workflow.get("case")
        if not isinstance(case, dict):
            return
        try:
            case_id = UUID(str(case["id"]))
        except (KeyError, ValueError):
            return
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "Карточка сохранена. Можно запустить проверяемый юридический анализ:",
                reply_markup=analysis_keyboard(case_id),
            )

    gateway_bot._send_workflow_report = wrapped_send_workflow_report
    _PATCHED = True


def build_application_with_analysis(token: str):
    settings = load_analysis_settings()
    application = gateway_bot.build_application(token)
    if settings is None:
        return application

    _install_report_analysis_button(settings)

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await analyze_case_callback(update, context, settings=settings)

    application.add_handler(
        CallbackQueryHandler(
            handler,
            pattern=(
                r"^case:analyze:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}$"
            ),
        ),
        group=-1,
    )
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    application = build_application_with_analysis(gateway_bot.load_token())
    application.run_polling(
        allowed_updates=gateway_bot.ALLOWED_UPDATES,
        bootstrap_retries=3,
        drop_pending_updates=False,
    )
