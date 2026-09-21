# ruff: noqa: RUF001
"""Confirmed sparse intake: presentation helpers, not legal/risk decisions.

The legacy extractor remains a candidate generator. Nothing from this module is
persisted until the person explicitly confirms the complete review card.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from legal_core.pseudonymization import pseudonymize_text

from telegram_gateway.case_wizard import parse_date_answer, parse_ruble_amount_to_kopecks
from telegram_gateway.quick_intake import contains_probable_person_name, extract_quick_intake

# Keep server-owned wizard states and field names: old drafts remain resumable.
FIELDS = {
    "INCIDENT": ("incident_type", "Тип ситуации"),
    "SERVICE_TYPE": ("service_type", "Услуга"),
    "SERVICE_DATE": ("service_date", "Дата услуги"),
    "INCIDENT_DATE": ("incident_date", "Дата проблемы"),
    "CLAIM_DATE": ("claim_date", "Дата первого обращения"),
    "PROBLEM_SUMMARY": ("problem_summary", "Описание"),
    "PATIENT_DEMAND": ("patient_demand", "Требование пациента"),
    "DEMAND_AMOUNT": ("demand_amount_kopecks", "Заявленная сумма"),
    "FORMAL_CLAIM": ("formal_claim", "Письменная претензия"),
    "CLAIM_RECEIVED_AT": ("claim_received_at", "Дата получения претензии"),
    "CLAIM_DEADLINE": ("response_deadline", "Срок из документа / сообщения"),
    "HARM": ("harm_claimed", "Заявление о вреде здоровью"),
    "HOSPITALIZATION": ("hospitalization", "Госпитализация"),
    "LAWYER": ("lawyer_contact", "Обращение представителя"),
    "REPRESENTATIVE_AUTHORITY": ("representative_authority", "Полномочия представителя"),
    "LAWYER_DEADLINE": ("response_deadline", "Срок из документа / сообщения"),
    "AUTHORITY": ("regulator_or_court", "Обращение органа или суда"),
    "AUTHORITY_KIND": ("authority_kind", "Орган или суд"),
    "AUTHORITY_DATE": ("authority_document_date", "Дата документа органа"),
    "AUTHORITY_DEADLINE": ("response_deadline", "Срок из документа / сообщения"),
    "REGULATOR_THREAT": ("regulator_threat", "Угроза обращения в орган / суд"),
    "DOCUMENTS": ("documents_status", "Документы по сообщению сотрудника"),
}
SIGNALS = {"YES": "Да", "NO": "Нет", "UNKNOWN": "Неизвестно"}
CHOICES = {
    "incident_type": {
        "QUALITY_COMPLAINT": "Качество лечения", "PAYMENT_DISPUTE": "Оплата / возврат",
        "INFORMED_CONSENT": "Согласие и документы", "PERSONAL_DATA": "Персональные данные",
        "OTHER": "Другая ситуация",
    },
    "patient_demand": {
        "NO_SPECIFIC_DEMAND": "Конкретных требований нет", "REWORK_DEMAND": "Переделка",
        "REFUND_DEMAND": "Возврат денег", "COMPENSATION_DEMAND": "Компенсация",
    },
    "documents_status": {"COMPLETE": "Есть", "PARTIAL": "Есть не всё", "NONE": "Нет"},
    **{field: SIGNALS for field in (
        "formal_claim", "harm_claimed", "hospitalization", "lawyer_contact",
        "representative_authority", "regulator_or_court", "regulator_threat",
    )},
}
PREFIXES = {
    "INCIDENT": "case:incident:", "PATIENT_DEMAND": "case:demand:",
    "FORMAL_CLAIM": "case:formal:", "HARM": "case:harm:",
    "HOSPITALIZATION": "case:hospital:", "LAWYER": "case:lawyer:",
    "REPRESENTATIVE_AUTHORITY": "case:representative:", "AUTHORITY": "case:authority:",
    "REGULATOR_THREAT": "case:regulator_threat:", "DOCUMENTS": "case:documents:",
}
DATE_FIELDS = frozenset({
    "service_date", "incident_date", "claim_date", "claim_received_at",
    "response_deadline", "authority_document_date",
})
TEXT_BOUNDS = {"service_type": (2, 120), "problem_summary": (10, 1500), "authority_kind": (2, 120)}
DEPENDENTS = {
    "patient_demand": ("demand_amount_kopecks",),
    "formal_claim": ("claim_received_at", "response_deadline"),
    "harm_claimed": ("hospitalization",),
    "lawyer_contact": ("representative_authority", "response_deadline"),
    "regulator_or_court": ("authority_kind", "authority_document_date", "response_deadline"),
}


def signal(value: object) -> object:
    return "YES" if value is True else "NO" if value is False else value


def valid_value(field: str, value: object) -> bool:
    if field in CHOICES:
        normalized = signal(value)
        return isinstance(normalized, str) and normalized in CHOICES[field]
    if field in DATE_FIELDS:
        if isinstance(value, str):
            return parse_date_answer(value, allow_future=field == "response_deadline") is not None
        if not isinstance(value, dict) or set(value) != {"date", "precision"}:
            return False
        if value["precision"] == "UNKNOWN":
            return value["date"] is None
        if value["precision"] not in {"EXACT", "APPROXIMATE"} or not isinstance(value["date"], str):
            return False
        return parse_date_answer(
            value["date"], allow_future=field == "response_deadline",
        ) is not None
    if field == "demand_amount_kopecks":
        return type(value) is int and 1 <= value <= 100_000_000_000
    if field in TEXT_BOUNDS:
        low, high = TEXT_BOUNDS[field]
        return isinstance(value, str) and low <= len(value.strip()) <= high
    return False


def active_states(data: dict[str, Any]) -> list[str]:
    states = list(FIELDS)[:7]
    if data.get("patient_demand") in ("REFUND_DEMAND", "COMPENSATION_DEMAND"):
        states.append("DEMAND_AMOUNT")
    states.append("FORMAL_CLAIM")
    if signal(data.get("formal_claim")) == "YES":
        states.extend(("CLAIM_RECEIVED_AT", "CLAIM_DEADLINE"))
    states.append("HARM")
    if signal(data.get("harm_claimed")) in ("YES", "UNKNOWN"):
        states.append("HOSPITALIZATION")
    states.append("LAWYER")
    if signal(data.get("lawyer_contact")) == "YES":
        states.extend(("REPRESENTATIVE_AUTHORITY", "LAWYER_DEADLINE"))
    states.append("AUTHORITY")
    if signal(data.get("regulator_or_court")) == "YES":
        states.extend(("AUTHORITY_KIND", "AUTHORITY_DATE", "AUTHORITY_DEADLINE"))
    states.extend(("REGULATOR_THREAT", "DOCUMENTS"))
    return states


def next_missing_state(data: dict[str, Any]) -> str:
    for state in active_states(data):
        field = FIELDS[state][0]
        if not valid_value(field, data.get(field)):
            return state
    return "CONFIRM"


def drop_field(data: dict[str, Any], field: str) -> None:
    data.pop(field, None)
    for dependent in DEPENDENTS.get(field, ()):
        data.pop(dependent, None)


def parse_answer(field: str, raw: str) -> object:
    if field in CHOICES:
        value = raw.upper()
        if value not in CHOICES[field]:
            raise ValueError("Выберите ответ кнопкой под текущим вопросом.")
        return value
    if field in DATE_FIELDS:
        value = raw.strip()
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
            try:
                value = datetime.strptime(value, "%d.%m.%Y").date().isoformat()
            except ValueError:
                pass
        parsed = parse_date_answer(value, allow_future=field == "response_deadline")
        if parsed is None:
            raise ValueError("Укажите дату ГГГГ-ММ-ДД, ДД.ММ.ГГГГ или «неизвестно».")
        return parsed
    if field == "demand_amount_kopecks":
        amount = parse_ruble_amount_to_kopecks(raw)
        if amount is None:
            raise ValueError("Укажите сумму в рублях числом, не более двух знаков после запятой.")
        return amount
    if field not in TEXT_BOUNDS:
        raise ValueError("Неизвестное поле.")
    if contains_probable_person_name(raw):
        raise ValueError("Уберите ФИО и другие персональные сведения из ответа.")
    value = pseudonymize_text(raw).text.strip()
    if not valid_value(field, value):
        low, high = TEXT_BOUNDS[field]
        raise ValueError(f"Введите от {low} до {high} символов без персональных данных.")
    return value


def confirmed_candidates(text: str, *, today: date | None = None) -> dict[str, Any]:
    """Return a review proposal, not confirmed facts, despite the intended destination."""
    result = extract_quick_intake(text, today=today)
    data = {key: value for key, value in result.candidate_data.items() if valid_value(key, value)}
    folded = result.sanitized_text.casefold()
    # The legacy matcher treats any 'госпитализ' occurrence as YES. Do not prefill it:
    # a human answers the conditional question, including negations and uncertain statements.
    data.pop("hospitalization", None)
    # An approximate date must never become exact simply because its digits are explicit.
    if re.search(r"примерно|ориентировочно|возможно|около\s+\d", folded):
        for field in DATE_FIELDS:
            data.pop(field, None)
    # Conservative conflict check after removing explicit negative phrases. Do not pick
    # a NO over a second positive/uncertain statement elsewhere in the same description.
    for field, negatives, topic in (
        ("formal_claim", r"письменной претензии нет|письменная претензия не поступала|"
         r"претензия не поступала", r"претенз"),
        ("harm_claimed", r"о вреде здоровью не заяв\w*|вред здоровью не заяв\w*", r"вред"),
        ("lawyer_contact", r"юрист не обращался|представитель не обращался|адвокат не обращался",
         r"юрист|представител|адвокат"),
    ):
        if data.get(field) == "NO" and re.search(topic, re.sub(negatives, "", folded)):
            data.pop(field, None)
    return data


def value_label(field: str, value: object) -> str:
    if field in CHOICES:
        return CHOICES[field].get(str(signal(value)), "Неизвестно")
    if field in DATE_FIELDS and isinstance(value, dict):
        precision = value.get("precision")
        if precision == "UNKNOWN":
            return "Неизвестно (точную дату ещё нужно уточнить)"
        return str(value.get("date")) + (" (примерно)" if precision == "APPROXIMATE" else "")
    if field == "demand_amount_kopecks" and type(value) is int:
        rubles, kopecks = divmod(value, 100)
        return f"{rubles:,},{kopecks:02d} ₽".replace(",", " ", 1) if rubles >= 1000 else (
            f"{rubles},{kopecks:02d} ₽"
        )
    return str(value)


def review_blocks(data: dict[str, Any], *, final: bool = False) -> list[str]:
    blocks = ["📋 Проверьте карточку" if final else "🧩 Проверьте, правильно ли понято описание"]
    seen: set[str] = set()
    for field, label in FIELDS.values():
        if field in data and field not in seen:
            blocks.append(f"{label}: {value_label(field, data[field])}")
            seen.add(field)
    missing = next_missing_state(data)
    if missing != "CONFIRM":
        blocks.append(f"После подтверждения уточню: {FIELDS[missing][1].lower()}. "
                      "Уже подтверждённое повторно вводить не потребуется.")
    blocks.append("Это сведения сотрудника, не установленное нарушение и не юридический вывод. "
                  "Неупомянутое не считается ответом «нет». Сумма — требование пациента, не долг.")
    if any(data.get(key) == "YES" for key in (
        "formal_claim", "harm_claimed", "lawyer_contact", "regulator_or_court",
    )):
        blocks.append("Есть сигнал срочности: передайте ситуацию ответственному специалисту, "
                      "не ожидая окончания автоматического анализа.")
    return blocks
