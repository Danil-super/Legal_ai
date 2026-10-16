# ruff: noqa: RUF001
"""Compact factual answer review with fixed labels, never model-generated UI copy."""

from typing import Any

from legal_core.intake_answer_values import preview_answer_value
from legal_core.intake_answer_wire import AnswerStatusResponse

from telegram_gateway.factual_safety_intake import LABELS, screening_summary
from telegram_gateway.guided_case_intake_v2 import (
    choice_items,
    clarification_review_blocks,
)

_LABELS = {"incomingKind": "Что поступило", "situationAreas": "Связано с",
           "affectedServices": "Услуги", "eventSummary": "Что произошло",
           "eventDate": "Когда произошло", "conflictStage": "Ход ситуации",
           "clinicActions": "Что сделала клиника", "healthSignals": "Последствия для здоровья"}
_REASONS = {
    "AMBIGUOUS": "Не удалось однозначно понять ответ. Укажите один конкретный факт "
    "или выберите вариант в анкете.",
    "CONTRADICTS_CONFIRMED": "Ответ расходится с другими подтверждёнными сведениями. "
    "Уточните, что именно изменилось, либо исправьте нужный шаг анкеты.",
    "OFF_QUESTION": "В ответе не удалось найти сведения по текущему вопросу. "
    "Ответьте именно на него либо выберите вариант в анкете.",
}


def review_blocks(status: AnswerStatusResponse, data: dict[str, Any]) -> list[str]:
    interpretation, question = status.interpretation, status.question
    if interpretation is None or question is None:
        raise ValueError("answer interpretation required")
    if interpretation.status == "UNRESOLVED":
        if interpretation.reason_code is None:
            raise ValueError("unresolved reason required")
        return [_REASONS[interpretation.reason_code],
                "Ответ не сохранён как факт. Можно написать точнее или заполнить вручную."]
    value, field = interpretation.value, question.target_field
    blocks = ["🧩 Проверьте, правильно ли понят ответ"]
    if field in {
        "incomingKind", "situationAreas", "conflictStage", "clinicActions", "healthSignals",
    }:
        labels = dict(choice_items(field))
        values = value if isinstance(value, list) else [value]
        if any(not isinstance(item, str) or item not in labels for item in values):
            raise ValueError("invalid closed answer")
        blocks.append(_LABELS[field] + ": " + "; ".join(labels[str(item)] for item in values))
    elif field == "affectedServices":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("invalid services")
        blocks.append("Услуги: " + "; ".join(str(item) for item in value))
    elif field == "eventSummary":
        if not isinstance(value, str):
            raise ValueError("invalid event summary")
        blocks.append("Что произошло: " + value)
    elif field == "eventDate":
        if not isinstance(value, dict):
            raise ValueError("invalid date")
        rendered = "точная дата пока неизвестна" if value["precision"] == "UNKNOWN" else (
            ("примерно " if value["precision"] == "APPROXIMATE" else "") + str(value["date"])
        )
        blocks.append("Когда произошло: " + rendered)
    elif field == "safetyScreening":
        subfield = question.screening_field
        if subfield == "amount":
            blocks.extend(screening_summary({"safetyScreening": {"amount": value}}))
        else:
            if subfield not in LABELS or not isinstance(value, str):
                raise ValueError("invalid safety answer")
            blocks.append(LABELS[subfield] + ": " + {
                "YES": "да", "NO": "нет", "UNKNOWN": "неизвестно",
            }[value])
    else:
        request = question.clarification_request
        if request is None:
            raise ValueError("clarification request required")
        answer = {**request.model_dump(mode="json", by_alias=True), "value": value}
        blocks.extend(clarification_review_blocks({
            **data, "analysisClarificationAnswers": {"answers": [answer]},
        }))
    preview = preview_answer_value(data, question, value)
    dependencies = []
    if field != "safetyScreening" and "safetyScreening" in data and (
        preview.get("safetyScreening") != data["safetyScreening"]
    ):
        dependencies.append("фактические уточнения о последствиях и требованиях")
    if field != "affectedServices" and "affectedServices" in data and (
        preview.get("affectedServices") != data["affectedServices"]
    ):
        dependencies.append("перечень услуг")
    if field != "analysisClarificationAnswers" and any(
        key in data and preview.get(key) != data[key]
        for key in ("analysisClarificationRequests", "analysisClarificationAnswers")
    ):
        dependencies.append("уточнения по изменённым услугам")
    if dependencies:
        blocks.append("После подтверждения потребуется заново уточнить: "
                      + "; ".join(dependencies) + ". Остальные сведения сохранятся.")
    blocks.append("Сохраним только после вашего подтверждения. "
                  "Перед рекомендациями вы проверите общую фабулу.")
    return blocks
