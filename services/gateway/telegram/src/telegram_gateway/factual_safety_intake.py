# ruff: noqa: RUF001
"""Ordinary factual questions; never interpret a claim, injury or legal deadline."""

from typing import Any

from legal_core.factual_safety_intake import (
    SCREENING_FIELDS,
    SCREENING_VERSION,
    FactualSafetyScreening,
    validate_partial_screening,
)
from pydantic import ValidationError

from telegram_gateway.case_wizard import parse_ruble_amount_to_kopecks

QUESTIONS = {
    "healthDeteriorationReported": "Сообщал ли пациент об ухудшении состояния или осложнении?",
    "hospitalizationReported": "Сообщал ли пациент о госпитализации в связи с этой ситуацией?",
    "representativeContact": "Обращался ли к клинике юрист или другой представитель пациента?",
    "writtenRequirementsReceived": "Получала ли клиника письменные требования пациента?",
    "authorityOrCourtDocumentReceived": "Получала ли клиника по этой ситуации документ суда "
    "или контролирующего органа?",
    "authorityReferralMentioned": "Говорил ли пациент об обращении в суд или контролирующий орган?",
    "moneyRequested": "Просил ли пациент вернуть деньги или выплатить компенсацию?",
    "amount": "Какую сумму просит пациент? Укажите рубли (например, 12000,50). "
    "Если сумма неизвестна, нажмите «Не знаю».",
}
LABELS = {
    "healthDeteriorationReported": "Сообщение об ухудшении/осложнении",
    "hospitalizationReported": "Сообщение о госпитализации",
    "representativeContact": "Обращение представителя",
    "writtenRequirementsReceived": "Письменные требования",
    "authorityOrCourtDocumentReceived": "Документ суда/органа",
    "authorityReferralMentioned": "Упоминание обращения в суд/орган",
    "moneyRequested": "Денежная просьба",
}


def screening_complete(data: dict[str, Any]) -> bool:
    try:
        FactualSafetyScreening.model_validate(data.get("safetyScreening"))
    except ValidationError:
        return False
    return True


def known_urgent_report(data: dict[str, Any]) -> bool:
    """UI shortcut only; Legal Core and approved policy decide actual escalation."""
    health = data.get("healthSignals")
    if (isinstance(health, list) and "HOSPITALIZATION" in health) or (
        data.get("incomingKind") == "AUTHORITY_OR_COURT_DOCUMENT"
    ):
        return True
    try:
        screening = validate_partial_screening(data.get("safetyScreening"))
    except ValueError:
        return False
    if any(screening.get(field) == "YES" for field in SCREENING_FIELDS[:5]):
        return True
    amount = screening.get("amount")
    return isinstance(amount, dict) and amount["amountKopecks"] >= 5_000_000


def explicitly_skip_other_questions(data: dict[str, Any]) -> None:
    """User requested known-fact confirmation; unanswered fields explicitly stay UNKNOWN."""
    if not known_urgent_report(data):
        raise ValueError("known-fact shortcut needs a confirmed reported event")
    screening = dict(data.get("safetyScreening", {"schemaVersion": SCREENING_VERSION}))
    for field in SCREENING_FIELDS:
        screening.setdefault(field, "UNKNOWN")
    screening.setdefault(
        "amount", "NOT_REQUESTED" if screening["moneyRequested"] == "NO" else "UNKNOWN"
    )
    FactualSafetyScreening.model_validate(screening)
    data["safetyScreening"] = screening


def screening_question(data: dict[str, Any]) -> tuple[str, str] | None:
    value = data.get("safetyScreening", {"schemaVersion": SCREENING_VERSION})
    try:
        screening = validate_partial_screening(value)
    except ValueError:
        return SCREENING_FIELDS[0], QUESTIONS[SCREENING_FIELDS[0]]
    for field in SCREENING_FIELDS:
        if field not in screening:
            return field, QUESTIONS[field]
    if screening.get("moneyRequested") == "YES" and "amount" not in screening:
        return "amount", QUESTIONS["amount"]
    return None if screening_complete(data) else ("moneyRequested", QUESTIONS["moneyRequested"])


def answer_screening(data: dict[str, Any], field: str, answer: str) -> None:
    if field not in {*SCREENING_FIELDS, "amount"}:
        raise ValueError("unknown factual question")
    screening = dict(data.get("safetyScreening", {"schemaVersion": SCREENING_VERSION}))
    if field == "amount":
        if screening.get("moneyRequested") != "YES":
            raise ValueError("an amount requires an explicit monetary request")
        screening[field] = (
            "UNKNOWN"
            if answer == "UNKNOWN"
            else {
                "amountKopecks": parse_ruble_amount_to_kopecks(answer),
                "currency": "RUB",
            }
        )
    else:
        if answer not in {"YES", "NO", "UNKNOWN"}:
            raise ValueError("use YES, NO or UNKNOWN")
        screening[field] = answer
        if field == "moneyRequested":
            screening.pop("amount", None)
            if answer in {"NO", "UNKNOWN"}:
                screening["amount"] = "NOT_REQUESTED" if answer == "NO" else "UNKNOWN"
    validate_partial_screening(screening)
    data["safetyScreening"] = screening


def previous_screening_question(data: dict[str, Any]) -> bool:
    previous = data.get("safetyScreening")
    if not isinstance(previous, dict):
        return False
    # The durable adapter takes a shallow before-snapshot. Never mutate its nested value.
    screening = dict(previous)
    if screening.get("moneyRequested") == "YES" and "amount" in screening:
        screening.pop("amount")
        data["safetyScreening"] = screening
        return True
    for field in reversed(SCREENING_FIELDS):
        if field in screening:
            screening.pop(field)
            if field == "moneyRequested":
                screening.pop("amount", None)
            data["safetyScreening"] = screening
            return True
    return False


def screening_summary(data: dict[str, Any]) -> list[str]:
    value = data.get("safetyScreening")
    if not isinstance(value, dict):
        return []
    labels = {"YES": "да", "NO": "нет", "UNKNOWN": "неизвестно"}
    lines = []
    for field, label in LABELS.items():
        answer = value.get(field)
        rendered_answer = (
            labels.get(answer, "не указано") if isinstance(answer, str) else "не указано"
        )
        lines.append(f"{label}: {rendered_answer}")
    amount = value.get("amount")
    if isinstance(amount, dict):
        kopecks = amount["amountKopecks"]
        lines.append(f"Запрошено: {kopecks // 100} руб. {kopecks % 100:02d} коп.")
    elif amount == "UNKNOWN":
        lines.append("Запрошенная сумма: неизвестно")
    return lines
