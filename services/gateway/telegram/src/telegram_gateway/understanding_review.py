# ruff: noqa: RUF001
"""Factual proposal copy, with no model/runtime details or legal conclusions."""

from typing import Any

from legal_core.intake_preparation_contracts import DraftSuggestions

from telegram_gateway.factual_safety_intake import screening_summary
from telegram_gateway.guided_case_intake_v2 import (
    choice_items,
    clarification_review_blocks,
)

_LABELS = {
    "incomingKind": "Что поступило", "situationAreas": "Связано с",
    "conflictStage": "Ход ситуации", "clinicActions": "Что сделала клиника",
    "healthSignals": "Последствия для здоровья",
}


def proposal_blocks(data: dict[str, Any], suggestions: DraftSuggestions) -> list[str]:
    """Render every one of the nine optional fields; no invisible acceptance."""
    proposed = {key: value for key, value in suggestions.model_dump(
        mode="json", by_alias=True,
    ).items() if value is not None and key != "sourceSpans"}
    blocks = ["🧩 Проверьте, верно ли понята ситуация"]
    for field, label in _LABELS.items():
        if field in proposed:
            values = proposed[field] if isinstance(proposed[field], list) else [proposed[field]]
            choices = dict(choice_items(field))
            blocks.append(label + ": " + "; ".join(choices[item] for item in values))
    if "affectedServices" in proposed:
        blocks.append("Услуги: " + "; ".join(proposed["affectedServices"]))
    if "eventDate" in proposed:
        value = proposed["eventDate"]
        rendered = "точная дата пока неизвестна" if value["precision"] == "UNKNOWN" else (
            ("примерно " if value["precision"] == "APPROXIMATE" else "") + value["date"]
        )
        blocks.append("Когда произошло: " + rendered)
    if "safetyScreening" in proposed:
        blocks.extend(screening_summary(proposed))
        if proposed["safetyScreening"].get("amount") == "NOT_REQUESTED":
            blocks.append("Сумма: не запрошена")
    if "analysisClarificationAnswers" in proposed:
        # Existing confirmed service names supply slots when the proposal only adds answers.
        blocks.extend(clarification_review_blocks({**data, **proposed}))
    if not proposed:
        blocks.append("Дополнительные сведения не распознаны; уточним их в анкете.")
    blocks.append("Подтвердите только то, что соответствует ситуации. "
                  "Неуказанные сведения не считаются отсутствующими. "
                  "После подтверждения уточним недостающее и покажем итоговую фабулу.")
    return blocks
