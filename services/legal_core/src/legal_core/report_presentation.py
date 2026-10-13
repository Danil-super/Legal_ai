"""Read-only Russian presentation of saved report data, shared by PDF and Telegram."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class ReportBlock:
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class ReportPresentation:
    header: tuple[str, ...]
    blocks: tuple[ReportBlock, ...]
    disclaimer: str
    source_caveats: tuple[str, ...]


_FACT_LABELS = {
    "INTAKE_VERSION": "Вариант анкеты",
    "INCOMING_COMMUNICATION": "Вид обращения",
    "INCOMING_SOURCE_STATUS": "Исходный текст или файл обращения",
    "SITUATION_AREAS": "Темы обращения",
    "AFFECTED_SERVICES": "Затронутые услуги",
    "EVENT_SUMMARY": "Описание ситуации",
    "EVENT_DATE": "Дата события",
    "CONFLICT_STAGE": "История обращений",
    "CLINIC_ACTIONS": "Действия клиники",
    "HEALTH_CONSEQUENCE_SIGNALS": "Последствия для здоровья",
    "CASE_MATERIALS_STATUS": "Материалы клиники",
    "FACTUAL_SAFETY_SCREENING": "Сведения для проверки срочности",
    "INCIDENT_TYPES": "Вид ситуации",
    "PRIMARY_INCIDENT_TYPE": "Основная ситуация",
    "SERVICE_TYPE": "Услуга",
    "SERVICE_DATE": "Дата оказания услуги",
    "INCIDENT_DATE": "Дата возникновения проблемы",
    "CLAIM_DATE": "Дата обращения",
    "PROBLEM_SUMMARY": "Описание проблемы",
    "PATIENT_DEMAND": "Требования пациента",
    "DEMAND_AMOUNT": "Запрошенная сумма",
    "FORMAL_CLAIM": "Письменная претензия",
    "CLAIM_RECEIVED_AT": "Дата получения претензии",
    "RESPONSE_DEADLINE": "Указанный срок ответа",
    "HARM_CLAIMED": "Сообщённое ухудшение здоровья",
    "HOSPITALIZATION": "Госпитализация",
    "LAWYER_CONTACT": "Обращение представителя",
    "REPRESENTATIVE_AUTHORITY": "Полномочия представителя",
    "REGULATOR_OR_COURT": "Документ суда или контролирующего органа",
    "REGULATOR_THREAT": "Намерение обратиться в контролирующий орган",
    "AUTHORITY_KIND": "Наименование суда или органа",
    "DOCUMENT_DATE": "Дата документа",
    "CLINIC_DOCUMENTS": "Документы по ситуации",
}
_ACTIONS = {
    "NOTHING_YET": "клиника пока не предпринимала действий",
    "INVITED_FOR_EXAMINATION": "пригласили на осмотр",
    "CONTINUING_TREATMENT": "продолжается лечение",
    "HELD_MEDICAL_COMMISSION": "провели врачебную комиссию",
    "OFFERED_CORRECTION": "предложили коррекцию",
    "OFFERED_REFUND": "предложили возврат",
    "REMOVED_MATERIAL": "удалили материал",
    "TERMINATED_CONTRACT": "прекратили договор",
    "SIGNED_AGREEMENT": "подписали соглашение",
    "OTHER": "другое действие",
}
_DEMANDS = {
    "REFUND_DEMAND": "возврат денег",
    "COMPENSATION_DEMAND": "компенсация",
    "NO_SPECIFIC_DEMAND": "конкретных требований пока нет",
    "REWORK_DEMAND": "повторное лечение",
    "NEGATIVE_REVIEW_PRESSURE": "обращение сопровождается негативным отзывом",
    "CORRECTION_DEMAND": "коррекция результата",
    "EXPLANATION_DEMAND": "разъяснение ситуации",
    "OTHER": "другое требование",
    "UNKNOWN": "требования не уточнены",
}
_RISK_LABELS = {
    "LOW": "Низкий риск",
    "MEDIUM": "Средний риск",
    "HIGH": "Высокий риск — требуется юрист",
    "CRITICAL": "Критический риск — требуется юрист",
    "UNAVAILABLE": "Оценка риска недоступна",
}
_RISK_REASONS = {
    "NO_ESCALATION_TRIGGER": (
        "По сохранённой оценке оснований для обязательной передачи юристу не выявлено."
    ),
    "HOSPITALIZATION_REPORTED": "Сообщена госпитализация.",
    "AUTHORITY_OR_COURT_DOCUMENT_REPORTED": "Получен документ суда или контролирующего органа.",
    "OFFICIAL_REGULATOR_OR_COURT_SIGNAL": (
        "Сообщено обращение суда или контролирующего органа."
    ),
    "LAWYER_OR_REPRESENTATIVE_CONTACT": "Обратился юрист или представитель пациента.",
    "FORMAL_CLAIM_RECEIVED": "Получена письменная претензия.",
    "WRITTEN_REQUIREMENTS_REPORTED": "Получены письменные требования.",
    "HARM_REPORTED": "Сообщено ухудшение здоровья; причинная связь этим не установлена.",
    "HEALTH_DETERIORATION_REPORTED": (
        "Сообщено ухудшение здоровья; причинная связь этим не установлена."
    ),
    "HIGH_DEMAND_AMOUNT": "Запрошенная сумма достигла порога сохранённой оценки риска.",
    "PATIENT_DEMAND_REQUIRES_REVIEW": "Требования пациента нуждаются в проверке.",
    "REGULATOR_THREAT_REPORTED": "Сообщено намерение обратиться в контролирующий орган.",
    "RELEVANT_DOCUMENT_MISSING": "Отсутствует относящийся к ситуации документ.",
}
_BLOCK_REASONS = {
    "LEGAL_CORPUS_NOT_READY": "Проверенные нормативные источники для анализа пока недоступны.",
    "INSUFFICIENT_FACTS": "Недостаточно сведений для юридического анализа.",
    "CRITICAL_FACTS_MISSING": "Недостаточно критичных сведений для юридического анализа.",
    "RISK_ASSESSMENT_UNAVAILABLE": "Сохранённая оценка риска недоступна.",
    "VERIFICATION_BLOCKED": "Юридический анализ не прошёл проверку доказательств.",
    "HUMAN_LEGAL_REVIEW_REQUIRED": "Для подготовки ответа требуется проверка юристом.",
    "RISK_POLICY_BLOCKS_EXTERNAL_DRAFT": "Сохранённая оценка риска не разрешает подготовку ответа.",
    "PATIENT_DRAFT_SAFETY_SIGNAL": "Ситуация требует проверки перед подготовкой ответа.",
}
_DOCUMENTS = {
    "CONTRACT": "Договор",
    "MEDICAL_RECORD": "Медицинская документация",
    "INFORMED_CONSENT": "Информированное согласие",
}
_DOCUMENT_STATUS = {
    "AVAILABLE": "есть по сведениям сотрудника",
    "MISSING": "отсутствует",
    "UNKNOWN": "наличие не уточнено",
}
_CURRENT_COPY_CAVEAT = (
    "Дата редакции и публикации не установлена; "
    "более ранняя применимость этой копии не подтверждена."
)


def _object(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _texts(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _date_text(value: object) -> str | None:
    precision = None
    if isinstance(value, Mapping):
        precision, value = value.get("precision"), value.get("date")
    if not isinstance(value, str) or precision == "UNKNOWN":
        return None
    try:
        formatted = date.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return None
    return f"примерно {formatted}" if precision == "APPROXIMATE" else formatted


def readable_source_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "Раздел источника не указан"
    labels = {
        "article": "статья",
        "part": "часть",
        "point": "пункт",
        "paragraph": "абзац",
        "section": "раздел",
        "chapter": "глава",
        "subpoint": "подпункт",
    }
    if re.search(r"[a-z_]+:", value):
        parts = re.split(r"[/; >]+", value)
        translated = []
        for part in parts:
            key, separator, number = part.partition(":")
            if not separator or key not in labels or not number:
                return "Раздел источника не распознан"
            translated.append(f"{labels[key]} {number}")
        return ", ".join(translated)
    return value


def risk_level_label(level: object) -> str:
    return _RISK_LABELS.get(str(level), "Уровень риска неизвестен.")


def risk_reason_description(code: str) -> str:
    return _RISK_REASONS.get(
        code,
        "Сохранённая причина риска не распознана; уточните её через ответственного сотрудника."
    )


def present_report(report: Mapping[str, Any]) -> ReportPresentation:
    """Format known saved fields; unknown internal identifiers never become advice."""
    case, summary = _object(report.get("case")), _object(report.get("summary"))
    facts = _object(report.get("facts"))
    availability = _object(summary.get("analysisAvailability"))
    ready = availability.get("status") == "READY"
    description = summary.get("neutralDescription")
    situation = [
        "По сведениям сотрудника: "
        + (description if isinstance(description, str) else "Описание не сохранено.")
    ]
    for key in ("SERVICE_TYPE", "AFFECTED_SERVICES"):
        value = facts.get(key)
        text = value if isinstance(value, str) else ", ".join(_texts(value))
        if text:
            situation.append(f"{_FACT_LABELS[key]}: {text}")
    for key in (
        "SERVICE_DATE",
        "EVENT_DATE",
        "INCIDENT_DATE",
        "CLAIM_DATE",
        "CLAIM_RECEIVED_AT",
        "DOCUMENT_DATE",
        "RESPONSE_DEADLINE",
    ):
        date_text = _date_text(facts.get(key))
        if date_text:
            suffix = (
                " (сведения сотрудника; срок по закону здесь не рассчитан)"
                if (key == "RESPONSE_DEADLINE")
                else ""
            )
            situation.append(f"{_FACT_LABELS[key]}: {date_text}{suffix}")
    demands = _texts(facts.get("PATIENT_DEMAND"))
    if demands:
        situation.append(
            "Требования пациента: "
            + "; ".join(
                _DEMANDS.get(item, "сохранённое требование не распознано") for item in demands
            )
        )
    money = _object(facts.get("DEMAND_AMOUNT"))
    amount = money.get("amountKopecks")
    if type(amount) is int and money.get("currency") == "RUB":
        situation.append(f"Запрошенная сумма: {amount // 100},{amount % 100:02d} рублей.")

    clinic = [
        _ACTIONS.get(item, "Сохранённое действие клиники не распознано.")
        for item in _texts(facts.get("CLINIC_ACTIONS"))
    ]
    if not clinic:
        clinic.append("Действия клиники в отчёте не указаны.")
    if facts.get("CASE_MATERIALS_STATUS") == "ATTACHED":
        clinic.append("Обезличенные материалы клиники приложены.")
    elif facts.get("CASE_MATERIALS_STATUS") == "NOT_ATTACHED":
        clinic.append("Материалы клиники не приложены.")
    for key, value in _object(facts.get("CLINIC_DOCUMENTS")).items():
        if key in _DOCUMENTS:
            clinic.append(
                f"{_DOCUMENTS[key]}: {_DOCUMENT_STATUS.get(str(value), 'статус неизвестен')}."
            )
    clinic_sources = _object(report.get("clinicDocuments")).get("sources", [])
    if ready and isinstance(clinic_sources, list) and clinic_sources:
        clinic.append("Документы клиники — внутренний контекст, не нормативные основания:")
        for source in clinic_sources:
            card = _object(source)
            if isinstance(card.get("documentTitle"), str):
                clinic.append(
                    f"• {card['documentTitle']}; "
                    f"{readable_source_path(card.get('structuralPath'))}."
                )

    missing = report.get("missingFacts", [])
    gaps = (
        [
            "Уточните: "
            + _FACT_LABELS.get(
                str(_object(item).get("factKey")), "сведения, не распознанные в этом отчёте"
            )
            + "."
            for item in missing
        ]
        if isinstance(missing, list)
        else []
    )
    risk = _object(report.get("risk"))
    level = str(risk.get("level"))
    handoff = level in {"HIGH", "CRITICAL"} or risk.get("escalationRequired") is True
    if ready:
        risk_lines = [risk_level_label(level)]
        risk_lines.extend(
            risk_reason_description(code)
            for code in _texts(risk.get("reasonCodes"))
        )
        if handoff:
            risk_lines.append(
                "Следующий шаг: передайте кейс юристу для проверки и подготовки позиции."
            )
        else:
            risk_lines.append(
                "Следующий шаг: проверьте сохранённые действия и черновик перед использованием."
            )
        risk_lines.extend(gaps)
    else:
        risk_lines = [
            "АНАЛИЗ ЗАБЛОКИРОВАН"
            if availability.get("status") is None or availability.get("status") == "BLOCKED"
            else "Статус анализа неизвестен.",
            _BLOCK_REASONS.get(
                str(availability.get("reasonCode")), "Причина недоступности анализа не распознана."
            ),
        ]
        risk_lines.extend(
            gaps or ["Отсутствие списка пробелов не подтверждает достаточность сведений."]
        )
        risk_lines.append(
            "Следующий шаг: Уточните недостающие сведения и повторите анализ."
            if gaps
            else "Следующий шаг: уточните доступность анализа через ответственного сотрудника."
        )

    recommendations = _object(report.get("recommendations"))
    actions = (
        _texts(recommendations.get("items"))
        if ready and recommendations.get("status") == "AVAILABLE"
        else []
    )
    plan = [f"{index}. {item}" for index, item in enumerate(actions, 1)]
    if not plan:
        plan = ["Подтверждённый юридический план в отчёте недоступен."]
    elif actions == ["Передайте карточку ответственному сотруднику для внутренней проверки."]:
        plan.append(
            "Сохранена только общая внутренняя проверка; "
            "подробные юридические действия не сохранены."
        )

    legal: list[str] = []
    caveats: list[str] = []
    basis = _object(report.get("legalBasis"))
    sources = basis.get("sources", []) if ready and basis.get("status") == "AVAILABLE" else []
    sources = sources if isinstance(sources, list) else []
    numbers = {
        str(_object(source).get("fragmentId")): index for index, source in enumerate(sources, 1)
    }
    conclusions = report.get("legalConclusions", []) if ready else []
    if isinstance(conclusions, list):
        for index, item in enumerate(conclusions, 1):
            conclusion = _object(item)
            citations = _texts(conclusion.get("evidenceFragmentIds"))
            if (
                isinstance(conclusion.get("text"), str)
                and citations
                and all(item in numbers for item in citations)
            ):
                legal.append(
                    f"{index}. {conclusion['text']} Основания: "
                    + ", ".join(f"[{numbers[item]}]" for item in citations)
                    + "."
                )
    if not legal:
        legal.append("Отдельные проверенные юридические выводы в этом отчёте не сохранены.")
    as_of = _date_text(_object(report.get("analysis")).get("asOfDate"))
    for index, source in enumerate(sources, 1):
        card = _object(source)
        title = card.get("documentTitle")
        if not isinstance(title, str):
            continue
        number = f" № {card['officialNumber']}" if card.get("officialNumber") else ""
        legal.append(
            f"[{index}] {title}{number}; {readable_source_path(card.get('structuralPath'))}."
        )
        start, end = _date_text(card.get("effectiveFrom")), _date_text(card.get("effectiveTo"))
        current = card.get("dateBasis") == "LAWYER_CURRENT_COPY"
        label = "Начало проверенной применимости копии:" if current else "Начало действия:"
        legal.append(
            f"{label} {start or 'неустановленной даты'}"
            + (f" до {end} (не включая эту дату)." if end else ".")
            + (f" Дата кейса: {as_of}." if as_of else "")
        )
        if current:
            legal.append(_CURRENT_COPY_CAVEAT)
            caveats.append(_CURRENT_COPY_CAVEAT)
        for limitation in _texts(card.get("extractionLimitations")):
            note = "Ограничение извлечения: " + limitation
            legal.append(note)
            caveats.append(note)
        if isinstance(card.get("sourceUrl"), str):
            legal.append("Источник: " + card["sourceUrl"])
    if not sources:
        legal.append("Проверенные нормативные основания недоступны.")

    draft = _object(report.get("draftResponse"))
    if (
        ready
        and not handoff
        and level in {"LOW", "MEDIUM"}
        and draft.get("status") == "AVAILABLE"
        and isinstance(draft.get("text"), str)
    ):
        draft_lines = ["Черновик: обязательна проверка человеком перед отправкой.", draft["text"]]
    else:
        reason = "HUMAN_LEGAL_REVIEW_REQUIRED" if handoff else str(draft.get("reasonCode"))
        draft_lines = [_BLOCK_REASONS.get(reason, "Черновик ответа недоступен.")]

    generated = report.get("generatedAt")
    try:
        prepared = datetime.fromisoformat(str(generated)).strftime("%d.%m.%Y %H:%M %z")
    except ValueError:
        prepared = "дата не сохранена"
    disclaimer = report.get("disclaimer")
    return ReportPresentation(
        header=(
            "План действий по обращению",
            f"Кейс {case.get('publicNumber', 'номер не сохранён')}",
            f"Подготовлено: {prepared}",
        ),
        blocks=tuple(
            ReportBlock(title, tuple(lines))
            for title, lines in (
                ("Ситуация и требования пациента", situation),
                ("Что уже сделала клиника", clinic),
                ("Риск и следующий шаг", risk_lines),
                ("План действий", plan),
                ("Проверенные выводы и нормативные основания", legal),
                ("Черновик ответа пациенту", draft_lines),
            )
        ),
        disclaimer=disclaimer if isinstance(disclaimer, str) else "",
        source_caveats=tuple(dict.fromkeys(caveats)),
    )


TELEGRAM_DRAFT_IN_PDF = "Текст черновика полностью в PDF."
TELEGRAM_SOURCE_LIMITATIONS_IN_PDF = (
    "Есть ограничения извлечения источников; полный перечень в PDF."
)
