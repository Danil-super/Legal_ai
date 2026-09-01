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
from telegram_gateway.case_wizard import LegalCoreClient

logger = logging.getLogger(__name__)
ANALYSIS_CALLBACK_PREFIX = "case:analyze:"
ANALYSIS_TIMEOUT_SECONDS = 90.0
_PATCHED = False
_READINESS_LABELS = {
    "CONTRACT": "договор на платные стоматологические услуги",
    "GENERAL_CONSENT": "общее информированное согласие (ИДС)",
    "WARRANTY_POLICY": "гарантийное положение",
    "IMPLANT_CONSENT": "ИДС на имплантацию / хирургическое вмешательство",
    "POST_IMPLANT_MEMO": "памятка после имплантации",
    "MEDICAL_RECORD_ACCESS": "порядок предоставления медицинских документов",
    "CLAIM_WORKFLOW": "внутренний регламент работы с претензиями",
    "PATIENT_RULES": "правила для пациентов",
}


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


def _missing_clinic_document_lines(payload: dict[str, Any]) -> list[str]:
    raw_readiness = payload.get("clinicDocumentReadiness", [])
    if not isinstance(raw_readiness, list):
        raise ValueError("analysis response has invalid clinic document readiness")

    missing: list[tuple[str, str]] = []
    for item in raw_readiness[:20]:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "NOT_AVAILABLE":
            continue
        code = _bounded_text(item.get("expectationCode"), limit=80)
        importance = _bounded_text(item.get("importance"), limit=24) or "SUPPORTING"
        if code is None:
            continue
        label = _READINESS_LABELS.get(code, code.replace("_", " ").lower())
        missing.append((importance, label))

    if not missing:
        return []

    ordered = sorted(
        dict.fromkeys(missing),
        key=lambda item: (
            {"CORE": 0, "SCENARIO": 1, "SUPPORTING": 2}.get(item[0], 3),
            item[1],
        ),
    )
    lines = ["", "📎 Для разбора полезно добавить в базу клиники:"]
    for importance, label in ordered[:8]:
        prefix = "⚠️" if importance == "CORE" else "•"
        lines.append(f"{prefix} {label}")
    lines.append("Это внутренний checklist, а не перечень обязательных по закону документов.")
    return lines


def telegram_analysis_summary(payload: dict[str, Any]) -> str:
    """Render only the server-verified analysis fields returned by Legal Core."""

    missing_document_lines = _missing_clinic_document_lines(payload)
    if payload.get("analysisAllowed") is not True:
        blocked_risk = _bounded_text(payload.get("riskLevel"), limit=24) or "UNAVAILABLE"
        lines = [
            "⚖️ Юридический анализ не прошёл проверку доказательств.",
            f"Риск: {blocked_risk}",
            "",
            "Бот не будет додумывать вывод. Проверьте недостающие факты или передайте кейс юристу.",
            *missing_document_lines,
        ]
        return "\n".join(lines)

    report = payload.get("report")
    if not isinstance(report, dict):
        raise ValueError("analysis response has no report")
    report_json = report.get("reportJson")
    if not isinstance(report_json, dict):
        raise ValueError("analysis response has no canonical report")

    case_data = report_json.get("case")
    risk_data = report_json.get("risk")
    recommendations_data = report_json.get("recommendations")
    legal_basis_data = report_json.get("legalBasis")
    draft_data = report_json.get("draftResponse")
    clinic_documents_data = report_json.get("clinicDocuments")
    if clinic_documents_data is None:
        clinic_documents_data = {"status": "NOT_USED", "sources": []}
    if not isinstance(case_data, dict):
        raise ValueError("canonical analysis report has no case block")
    if not isinstance(risk_data, dict):
        raise ValueError("canonical analysis report has no risk block")
    if not isinstance(recommendations_data, dict):
        raise ValueError("canonical analysis report has no recommendations block")
    if not isinstance(legal_basis_data, dict):
        raise ValueError("canonical analysis report has no legal basis block")
    if not isinstance(draft_data, dict):
        raise ValueError("canonical analysis report has no draft block")
    if not isinstance(clinic_documents_data, dict):
        raise ValueError("canonical analysis report has invalid clinic document block")

    public_number = _bounded_text(case_data.get("publicNumber"), limit=64)
    risk_level = _bounded_text(risk_data.get("level"), limit=24)
    reason_codes = risk_data.get("reasonCodes")
    action_items = recommendations_data.get("items")
    sources = legal_basis_data.get("sources")
    clinic_sources = clinic_documents_data.get("sources")
    clinic_status = _bounded_text(clinic_documents_data.get("status"), limit=24)
    draft_status = _bounded_text(draft_data.get("status"), limit=32)
    draft_reason = _bounded_text(draft_data.get("reasonCode"), limit=80)
    draft_text = _bounded_text(draft_data.get("text"), limit=1_600)
    draft_policy_version = _bounded_text(draft_data.get("policyVersion"), limit=80)
    if (
        public_number is None
        or risk_level is None
        or not isinstance(reason_codes, list)
        or not isinstance(action_items, list)
        or not isinstance(sources, list)
        or not isinstance(clinic_sources, list)
        or clinic_status is None
        or draft_status is None
    ):
        raise ValueError("canonical analysis report has invalid fields")
    if clinic_status == "USED" and not clinic_sources:
        raise ValueError("used clinic document context has no sources")
    if draft_status == "AVAILABLE" and draft_text is None:
        raise ValueError("available patient draft has no text")

    lines = [
        f"⚖️ АНАЛИЗ {public_number}",
        f"Риск: {risk_level}",
    ]
    if risk_data.get("escalationRequired") is True:
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

    clinic_lines: list[str] = []
    if clinic_status == "USED":
        for source in clinic_sources[:6]:
            if not isinstance(source, dict):
                continue
            title = _bounded_text(source.get("documentTitle"), limit=160)
            doc_type = _bounded_text(source.get("documentType"), limit=80)
            path = _bounded_text(source.get("structuralPath"), limit=100)
            version_no = source.get("versionNo")
            if title and doc_type and path and isinstance(version_no, int):
                clinic_lines.append(f"• {title}, v{version_no} · {doc_type} · {path}")
    if clinic_lines:
        lines.extend(
            [
                "",
                "📄 Документы клиники (внутренний контекст):",
                *clinic_lines,
                "Не являются нормативной правовой основой.",
            ]
        )

    lines.extend(missing_document_lines)

    if draft_status == "AVAILABLE" and draft_text is not None:
        lines.extend(["", "💬 Черновик ответа пациенту:", draft_text])
        lines.append("⚠️ Перед отправкой текст должен проверить сотрудник клиники.")
    else:
        lines.extend(["", f"Черновик пациенту: {draft_status}"])
        if draft_reason:
            lines.append(f"Причина: {draft_reason}")
    if draft_policy_version:
        lines.append(f"Draft policy: {draft_policy_version}")
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


def _install_report_analysis_button() -> None:
    global _PATCHED
    if _PATCHED:
        return
    original = gateway_bot._send_workflow_report

    async def wrapped_send_workflow_report(
        update: Update,
        client: LegalCoreClient,
        workflow: dict[str, Any],
        actor_id: int,
    ) -> None:
        await original(update, client, workflow, actor_id)
        case_data = workflow.get("case")
        if not isinstance(case_data, dict):
            return
        try:
            case_id = UUID(str(case_data["id"]))
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


def build_application_with_analysis(token: str) -> gateway_bot.TelegramApplication:
    settings = load_analysis_settings()
    application = gateway_bot.build_application(token)
    if settings is None:
        return application

    _install_report_analysis_button()

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
