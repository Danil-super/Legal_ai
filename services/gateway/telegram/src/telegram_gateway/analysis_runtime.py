# ruff: noqa: RUF001
"""Optional Telegram runtime extension for evidence-gated legal analysis."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError, LegalCoreClient
from telegram_gateway.escalation_workspace import show_workspace, workspace_action
from telegram_gateway.legal_conclusion_display import legal_conclusion_lines
from telegram_gateway.risk_labels import risk_reason_label
from telegram_gateway.ui import back_keyboard

logger = logging.getLogger(__name__)
ANALYSIS_CALLBACK_PREFIX = "case:analyze:"
ESCALATION_CALLBACK_PREFIX = "case:escalation:"
ESCALATION_QUEUE_CALLBACK = "case:escalations"
ESCALATION_DISCUSSION_CLOSE_CALLBACK = "case:discussion:close"
ESCALATION_DISCUSSION_KEY = "escalation_discussion_id"
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
        [
            [InlineKeyboardButton("⚖️ Запустить юридический анализ", callback_data=callback)],
            *back_keyboard().inline_keyboard,
        ]
    )


def escalation_discussion_keyboard(escalation_id: UUID) -> InlineKeyboardMarkup:
    callback = f"{ESCALATION_CALLBACK_PREFIX}{escalation_id}"
    if len(callback.encode()) > 64:
        raise ValueError("Telegram escalation callback is too long")
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💬 Обсудить с юристом", callback_data=callback)],
            [InlineKeyboardButton("⚖️ Критические кейсы", callback_data=ESCALATION_QUEUE_CALLBACK)],
            *back_keyboard().inline_keyboard,
        ]
    )


def _active_discussion_keyboard(escalation_id: UUID) -> InlineKeyboardMarkup:
    callback = f"{ESCALATION_CALLBACK_PREFIX}{escalation_id}"
    if len(callback.encode()) > 64:
        raise ValueError("Telegram escalation callback is too long")
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Обновить диалог", callback_data=callback)],
            [
                InlineKeyboardButton(
                    "← К критическим кейсам", callback_data=ESCALATION_QUEUE_CALLBACK
                ),
                InlineKeyboardButton("Закрыть", callback_data=ESCALATION_DISCUSSION_CLOSE_CALLBACK),
            ],
            *back_keyboard().inline_keyboard,
        ]
    )


def _bounded_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def analysis_error_message(code: str) -> str:
    messages = {
        "INSUFFICIENT_FACTS": "В кейсе не хватает обязательных фактов.",
        "ANALYSIS_DATE_UNCERTAIN": (
            "Не удалось определить применимую редакцию закона: уточните точную дату "
            "обращения, события или оказания услуги. Создайте новый кейс с уточнённой датой."
        ),
        "LEGAL_EVIDENCE_UNAVAILABLE": "Для этого кейса пока не хватает одобренной правовой базы.",
        "RISK_POLICY_NOT_READY": "Политика риска пока не активирована.",
        "ANALYSIS_CONTEXT_STALE": "Кейс изменился во время анализа. Запустите проверку ещё раз.",
        "ANALYSIS_PROVIDER_UNAVAILABLE": "ИИ-провайдер временно недоступен.",
        "CASE_ANALYSIS_ALREADY_COMPLETED": (
            "Анализ по этому кейсу уже сформирован. Используйте последнюю карточку результата."
        ),
    }
    return messages.get(code, "Не удалось безопасно завершить анализ.")


def escalation_id_from_analysis(payload: dict[str, Any]) -> UUID | None:
    """Read a server-issued escalation pointer without deriving one from case data."""

    required = payload.get("escalationRequired")
    raw_id = payload.get("escalationId")
    if required is not True:
        if raw_id is not None:
            raise ValueError("non-escalated analysis includes an escalation id")
        return None
    try:
        return UUID(str(raw_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("escalated analysis has no valid escalation id") from exc


def _queue_items(payload: dict[str, Any]) -> list[tuple[UUID, str]]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("escalation queue has invalid items")
    items: list[tuple[UUID, str]] = []
    for item in raw_items[:100]:
        if not isinstance(item, dict):
            continue
        try:
            escalation_id = UUID(str(item.get("escalationId")))
        except (TypeError, ValueError, AttributeError):
            continue
        public_number = _bounded_text(item.get("publicNumber"), limit=40)
        risk_level = _bounded_text(item.get("riskLevel"), limit=12)
        if public_number is None or risk_level not in {"HIGH", "CRITICAL"}:
            continue
        items.append((escalation_id, f"{public_number} · {risk_level}"[:60]))
    return items


def telegram_escalation_queue_summary(payload: dict[str, Any]) -> str:
    items = _queue_items(payload)
    if not items:
        return "⚖️ КРИТИЧЕСКИЕ КЕЙСЫ\n\nОткрытых кейсов для юридической проверки нет."
    return (
        "⚖️ КРИТИЧЕСКИЕ КЕЙСЫ\n\n"
        "В списке — только номер кейса и уровень риска. Выберите кейс, чтобы открыть "
        "внутренний обезличенный диалог."
    )


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
    draft_text = _bounded_text(draft_data.get("text"), limit=8_000)
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
        risk_reason_label(item)
        for item in reason_codes[:8] if isinstance(item, str) and item.strip()
    ]
    if safe_reasons:
        lines.extend(["", "Почему:", *(f"• {item}" for item in safe_reasons)])

    lines.extend(legal_conclusion_lines(report_json))

    safe_actions = [
        item[:500] for item in action_items[:8] if isinstance(item, str) and item.strip()
    ]
    if safe_actions:
        lines.extend(["", "Что сделать:", *(f"• {item}" for item in safe_actions)])

    source_lines: list[str] = []
    # Canonical READY reports already contain only verified, cited legal sources.
    for source_index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            continue
        title = _bounded_text(source.get("documentTitle"), limit=180)
        path = _bounded_text(source.get("structuralPath"), limit=100)
        url = _bounded_text(source.get("sourceUrl"), limit=500)
        if title and path and url:
            source_lines.append(f"[{source_index}] {title}, {path}\n  {url}")
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

    return "\n".join(lines)


def telegram_lawyer_handoff_summary(payload: dict[str, Any]) -> str | None:
    """Build a copyable, de-identified handoff only for a verified escalation.

    The handoff intentionally excludes the free-text case description, recommendations, patient
    draft and clinic-document text: those fields may contain patient data. A clinic administrator
    chooses a separately agreed protected channel for any further exchange with the lawyer.
    """

    if payload.get("analysisAllowed") is not True or payload.get("escalationRequired") is not True:
        return None

    report = payload.get("report")
    if not isinstance(report, dict):
        raise ValueError("analysis response has no report")
    report_json = report.get("reportJson")
    if not isinstance(report_json, dict):
        raise ValueError("analysis response has no canonical report")

    case_data = report_json.get("case")
    risk_data = report_json.get("risk")
    legal_basis_data = report_json.get("legalBasis")
    if (
        not isinstance(case_data, dict)
        or not isinstance(risk_data, dict)
        or not isinstance(legal_basis_data, dict)
    ):
        raise ValueError("canonical analysis report has invalid handoff fields")

    public_number = _bounded_text(case_data.get("publicNumber"), limit=64)
    risk_level = _bounded_text(risk_data.get("level"), limit=24)
    reason_codes = risk_data.get("reasonCodes")
    sources = legal_basis_data.get("sources")
    if (
        public_number is None
        or risk_level not in {"HIGH", "CRITICAL"}
        or risk_data.get("escalationRequired") is not True
        or not isinstance(reason_codes, list)
        or not isinstance(sources, list)
    ):
        raise ValueError("canonical analysis report is not a verified escalation")

    lines = [
        f"⚖️ ПАКЕТ ДЛЯ ЮРИСТА · {public_number}",
        f"Риск: {risk_level}",
        "Статус: требуется юридическая проверка.",
    ]
    safe_reasons = [
        risk_reason_label(item)
        for item in reason_codes[:8] if isinstance(item, str) and item.strip()
    ]
    if safe_reasons:
        lines.extend(["", "Триггеры эскалации:", *(f"• {item}" for item in safe_reasons)])

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
        lines.extend(["", "Проверенная правовая основа:", *source_lines])

    missing_document_lines = _missing_clinic_document_lines(payload)
    if missing_document_lines:
        lines.extend(missing_document_lines)

    lines.extend(
        [
            "",
            "В пакет не включены описание обращения, ФИО, контакты, меддокументы "
            "и черновик ответа пациенту.",
            "Передавайте дополнительные материалы только через согласованный защищённый "
            "канал клиники. Автоматическая отправка отключена.",
        ]
    )
    return "\n".join(lines)


def telegram_analysis_messages(payload: dict[str, Any]) -> tuple[str, ...]:
    """Return the user-visible verified card and, when required, its safe lawyer handoff."""

    summary = telegram_analysis_summary(payload)
    handoff = telegram_lawyer_handoff_summary(payload)
    texts = (summary,) if handoff is None else (summary, handoff)
    messages: list[str] = []
    for text in texts:
        # Telegram counts UTF-16 code units, so astral emoji occupy two units.
        # Split without dropping any text: a legal draft or its review warning
        # must never disappear because the report exceeds one message.
        start = 0
        units = 0
        for index, character in enumerate(text):
            width = 2 if ord(character) > 0xFFFF else 1
            if units + width > 4_000:
                messages.append(text[start:index])
                start = index
                units = 0
            units += width
        messages.append(text[start:])
    return tuple(messages)


async def _show_escalation_queue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *, status: str = "OPEN", before: UUID | None = None,
) -> None:
    gateway_bot._clear_pending_inputs(context)
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None:
        await gateway_bot._reply(update, "Не удалось определить пользователя.")
        return
    try:
        payload = await gateway_bot._legal_core(context).list_case_escalations(
            actor_id, status=status, before=before, limit=20,
        )
        items = _queue_items(payload)
    except (LegalCoreApiError, ValueError) as exc:
        logger.warning("case escalation queue failed: %s", type(exc).__name__)
        await gateway_bot._reply(
            update, "⚠️ Не удалось загрузить критические кейсы. Попробуйте позже."
        )
        return

    rows = [
        [
            InlineKeyboardButton(
                label,
                callback_data=f"{ESCALATION_CALLBACK_PREFIX}{escalation_id}",
            )
        ]
        for escalation_id, label in items[:20]
    ]
    if payload.get("nextBefore"):
        cursor = UUID(str(payload["nextBefore"]))
        rows.append([InlineKeyboardButton("Далее →", callback_data=f"esc:q:{status}:{cursor}")])
    other_status = "RESOLVED" if status == "OPEN" else "OPEN"
    rows.append([InlineKeyboardButton(
        "🗄 Завершённые" if status == "OPEN" else "⚖️ Открытые",
        callback_data=f"esc:q:{other_status}:first",
    )])
    rows.extend(list(row) for row in back_keyboard().inline_keyboard)
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            ("🗄 ЗАВЕРШЁННЫЕ КЕЙСЫ\n\nВыберите кейс для просмотра истории."
             if status == "RESOLVED" else telegram_escalation_queue_summary(payload)),
            reply_markup=InlineKeyboardMarkup(rows) if rows else None,
        )


async def show_escalation_queue_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await gateway_bot._answer_callback(update) != ESCALATION_QUEUE_CALLBACK:
        raise ApplicationHandlerStop
    await _show_escalation_queue(update, context)
    raise ApplicationHandlerStop


async def show_escalation_queue_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _show_escalation_queue(update, context)


async def open_escalation_discussion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    actor_id = gateway_bot._actor_id(update)
    if query is None or actor_id is None or not isinstance(query.data, str):
        raise ApplicationHandlerStop
    await query.answer()
    if gateway_bot.WIZARD_DATA_KEY in gateway_bot._user_data(context):
        await gateway_bot._reply(
            update,
            "Сначала вернитесь в главное меню: текущий черновик сохранён. "
            "Затем откройте обсуждение нужного кейса.",
            reply_markup=back_keyboard(),
        )
        raise ApplicationHandlerStop
    gateway_bot._clear_pending_inputs(context)
    try:
        escalation_id = UUID(query.data.removeprefix(ESCALATION_CALLBACK_PREFIX))
        await show_workspace(update, context, escalation_id)
    except (LegalCoreApiError, ValueError) as exc:
        logger.warning("case escalation discussion open failed: %s", type(exc).__name__)
        await gateway_bot._reply(
            update, "⚠️ Этот критический кейс недоступен или диалог не загрузился."
        )
        raise ApplicationHandlerStop from exc

    raise ApplicationHandlerStop


async def close_escalation_discussion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await gateway_bot._answer_callback(update) != ESCALATION_DISCUSSION_CLOSE_CALLBACK:
        raise ApplicationHandlerStop
    gateway_bot._user_data(context).pop(ESCALATION_DISCUSSION_KEY, None)
    gateway_bot._user_data(context).pop("escalation_resolution_pending", None)
    await gateway_bot._reply(
        update,
        "Диалог закрыт в этом чате. Сообщения сохранены во внутреннем журнале кейса.",
        reply_markup=back_keyboard(),
    )
    raise ApplicationHandlerStop


async def post_escalation_discussion_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user_data = gateway_bot._user_data(context)
    raw_id = user_data.get(ESCALATION_DISCUSSION_KEY)
    if not isinstance(raw_id, str):
        return
    if any(key in user_data for key in (
        gateway_bot.WIZARD_DATA_KEY, "quick_intake_pending", "quick_intake_candidate",
        gateway_bot.ADMIN_GRANT_ACCESS_KEY, gateway_bot.ADMIN_GRANT_PILOT_KEY,
        gateway_bot.TEAM_MEMBER_ROLE_KEY,
    )):
        user_data.pop(ESCALATION_DISCUSSION_KEY, None)
        user_data.pop("escalation_resolution_pending", None)
        return
    actor_id = gateway_bot._actor_id(update)
    message = update.effective_message
    raw_text = message.text if message is not None else None
    if actor_id is None or not isinstance(raw_text, str):
        raise ApplicationHandlerStop
    try:
        escalation_id = UUID(raw_id)
        resolving = user_data.get("escalation_resolution_pending") == raw_id
        if resolving:
            await gateway_bot._legal_core(context).resolve_escalation(
                escalation_id, actor_id, body=raw_text,
            )
            user_data.pop(ESCALATION_DISCUSSION_KEY, None)
            user_data.pop("escalation_resolution_pending", None)
        else:
            await gateway_bot._legal_core(context).post_escalation_discussion_message(
                escalation_id, actor_id, body=raw_text,
            )
    except LegalCoreApiError as exc:
        logger.warning("case escalation discussion post failed: %s", exc.code)
        if exc.code == "DIRECT_IDENTIFIER_NOT_ALLOWED":
            detail = "Не сохраняю сообщение: удалите ФИО, контакты и номера документов пациента."
        elif exc.code in {"ESCALATION_NOT_FOUND", "ESCALATION_RESOLVED"}:
            user_data.pop(ESCALATION_DISCUSSION_KEY, None)
            user_data.pop("escalation_resolution_pending", None)
            detail = "Этот кейс больше недоступен для обсуждения."
        else:
            detail = "Не удалось сохранить сообщение. Попробуйте ещё раз позже."
        await gateway_bot._reply(update, f"⚠️ {detail}")
        raise ApplicationHandlerStop from exc

    await gateway_bot._reply(
        update,
        ("✅ Обращение завершено. Итог сохранён в истории." if resolving else
         "✅ Сообщение добавлено. Можно написать следующий обезличенный вопрос или ответ."),
        reply_markup=back_keyboard() if resolving else _active_discussion_keyboard(escalation_id),
    )
    raise ApplicationHandlerStop


async def analyze_case_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    settings: AnalysisSettings,
) -> None:
    from telegram_gateway.analysis_jobs_runtime import enqueue_analysis

    del settings  # Model execution belongs to the durable worker, not this update handler.
    query = update.callback_query
    actor = update.effective_user
    if query is None or actor is None or not isinstance(query.data, str):
        raise ApplicationHandlerStop
    await query.answer()
    try:
        case_id = UUID(query.data.removeprefix(ANALYSIS_CALLBACK_PREFIX))
        await enqueue_analysis(update, context, case_id)
    except ValueError as exc:
        logger.warning("case analysis failed: %s", type(exc).__name__)
        await gateway_bot._reply(update, "⚠️ Ответ анализа не прошёл внутреннюю проверку.")
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
    gateway_key = gateway_bot.load_legal_editor_gateway_key()
    if gateway_key is not None:
        from telegram_gateway.escalation_notifications_runtime import (
            install_escalation_notifications,
        )

        install_escalation_notifications(application, gateway_key)
    if settings is not None:
        from telegram_gateway.analysis_jobs_runtime import install_analysis_jobs

        install_analysis_jobs(application, settings.internal_key)
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
    application.add_handler(
        CallbackQueryHandler(show_escalation_queue_callback, pattern=r"^case:escalations$"),
        group=-1,
    )
    application.add_handler(
        CallbackQueryHandler(
            open_escalation_discussion,
            pattern=(
                r"^case:escalation:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}$"
            ),
        ),
        group=-1,
    )
    application.add_handler(
        CallbackQueryHandler(
            close_escalation_discussion,
            pattern=r"^case:discussion:close$",
        ),
        group=-1,
    )
    application.add_handler(CommandHandler("escalations", show_escalation_queue_command), group=-1)
    application.add_handler(CallbackQueryHandler(workspace_action, pattern=r"^esc:"), group=-1)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, post_escalation_discussion_message),
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
