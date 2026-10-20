# ruff: noqa: RUF001
"""Read-only presentation of authorized case facts; never legal qualification."""

from typing import Any

from telegram_gateway.factual_safety_intake import screening_summary
from telegram_gateway.guided_case_intake_v2 import choice_items, clarification_review_blocks

_CHOICES = {
    "INCOMING_COMMUNICATION": ("Что поступило", "incomingKind"),
    "INCOMING_SOURCE_STATUS": ("Материал обращения", "incomingSourceStatus"),
    "SITUATION_AREAS": ("Темы обращения", "situationAreas"),
    "CONFLICT_STAGE": ("Ход ситуации", "conflictStage"),
    "CLINIC_ACTIONS": ("Действия клиники", "clinicActions"),
    "HEALTH_CONSEQUENCE_SIGNALS": ("Последствия для здоровья", "healthSignals"),
    "CASE_MATERIALS_STATUS": ("Материалы клиники", "caseMaterialsStatus"),
}
_LABELS = {
    "EVENT_SUMMARY": "Описание ситуации", "PROBLEM_SUMMARY": "Описание ситуации",
    "AFFECTED_SERVICES": "Услуги", "SERVICE_TYPE": "Услуга",
    "EVENT_DATE": "Дата обращения по ситуации", "SERVICE_DATE": "Дата услуги",
    "INCIDENT_DATE": "Дата проблемы", "CLAIM_DATE": "Дата обращения",
    "CLAIM_RECEIVED_AT": "Дата получения претензии", "DOCUMENT_DATE": "Дата документа",
    "RESPONSE_DEADLINE": "Указанный срок ответа (не расчёт по закону)",
    "PATIENT_DEMAND": "Требование пациента", "DEMAND_AMOUNT": "Запрошенная сумма",
    "HOSPITALIZATION": "Госпитализация", "HARM_CLAIMED": "Сообщение о вреде здоровью",
    "FORMAL_CLAIM": "Письменная претензия", "LAWYER_CONTACT": "Обращение представителя",
}
_DEMANDS = {
    "REFUND_DEMAND": "возврат денег", "COMPENSATION_DEMAND": "компенсация",
    "NO_SPECIFIC_DEMAND": "конкретных требований пока нет", "REWORK_DEMAND": "повторное лечение",
    "CORRECTION_DEMAND": "коррекция", "EXPLANATION_DEMAND": "разъяснение ситуации",
    "OTHER": "другое требование", "UNKNOWN": "неизвестно",
}


def _value(value: object) -> str:
    if isinstance(value, str):
        return {"YES": "да", "NO": "нет", "UNKNOWN": "неизвестно"}.get(value, value)
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
        if value.get("precision") == "UNKNOWN":
            return "дата неизвестна"
        date_value = value.get("date")
        if isinstance(date_value, str) and value.get("precision") in {"EXACT", "APPROXIMATE"}:
            return ("примерно " if value["precision"] == "APPROXIMATE" else "") + date_value
        amount = value.get("amountKopecks")
        if type(amount) is int and value.get("currency") == "RUB":
            return f"{amount // 100} руб. {amount % 100:02d} коп."
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "; ".join(value)
    return "формат сведений не распознан"


def case_fact_blocks(facts: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    unknown = False
    for key, value in facts.items():
        if key in {"INTAKE_VERSION", "FACTUAL_SAFETY_SCREENING", "ANALYSIS_CLARIFICATION_ANSWERS"}:
            continue
        if key in _CHOICES:
            label, field = _CHOICES[key]
            choices = dict(choice_items(field))
            values = value if isinstance(value, list) else [value]
            rendered = "; ".join(choices.get(item, "сведения не распознаны")
                                 if isinstance(item, str) else "сведения не распознаны"
                                 for item in values)
        elif key in _LABELS:
            label = _LABELS[key]
            if key == "PATIENT_DEMAND" and isinstance(value, list):
                rendered = "; ".join(_DEMANDS.get(item, "требование не распознано")
                                     if isinstance(item, str) else "требование не распознано"
                                     for item in value)
            else:
                rendered = _value(value)
        else:
            unknown = True
            continue
        blocks.append(f"{label}: {rendered or 'не указано'}")
    data = {"affectedServices": facts.get("AFFECTED_SERVICES")}
    try:
        blocks.extend(screening_summary({"safetyScreening": facts.get("FACTUAL_SAFETY_SCREENING")}))
        blocks.extend(clarification_review_blocks({
            **data, "analysisClarificationAnswers": facts.get("ANALYSIS_CLARIFICATION_ANSWERS",
                                                             {"answers": []}),
        }))
    except (ValueError, KeyError, TypeError):
        unknown = True
    if unknown:
        blocks.append("Часть дополнительных сведений не отображена; нужна проверка карточки.")
    return blocks
