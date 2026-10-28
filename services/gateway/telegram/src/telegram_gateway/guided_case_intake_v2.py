# ruff: noqa: RUF001
"""Pure, versioned presentation rules for the ordinary-language v2 case intake.

This module intentionally has no Telegram, database, model or legal-retrieval side
effects. It is the small deterministic contract that the durable gateway adapter
will persist and Legal Core will later validate. Keeping it pure makes it possible
to prove that the user is not asked to make a legal qualification.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from contextlib import suppress
from datetime import date
from typing import Any

from legal_core.analysis_clarifications import (
    AnalysisClarificationAnswers,
    ClarificationAnswer,
    ClarificationQuestionId,
    ClarificationRequest,
    ClarificationRequestBatch,
    ServiceProvidedDate,
    intake_clarification_requests,
)
from legal_core.intake_service_scope import (
    invalidate_service_clarification_answers as invalidate_service_scope,
)
from legal_core.pseudonymization import pseudonymize_text

from telegram_gateway.case_wizard import confirmed_clarification_answers, parse_date_answer
from telegram_gateway.factual_safety_intake import (
    known_urgent_report,
    screening_complete,
    screening_summary,
)
from telegram_gateway.quick_intake import contains_probable_person_name

V2_INCOMING = "INCOMING"
V2_SITUATION = "SITUATION"
V2_SERVICES = "SERVICES"
V2_EVENT = "EVENT"
V2_EVENT_DATE = "EVENT_DATE"
V2_CHRONOLOGY = "CHRONOLOGY"
V2_CLINIC_ACTIONS = "CLINIC_ACTIONS"
V2_HEALTH = "HEALTH"
V2_MATERIALS = "MATERIALS"
V2_SAFETY = "SAFETY"
V2_ANALYSIS_CLARIFICATION = "ANALYSIS_CLARIFICATION"
V2_SUMMARY = "SUMMARY"
V2_CONFIRM = "V2_CONFIRM"

_BASE_STATES = (
    V2_INCOMING,
    V2_SITUATION,
    V2_EVENT,
    V2_EVENT_DATE,
    V2_CHRONOLOGY,
    V2_CLINIC_ACTIONS,
    V2_HEALTH,
    V2_MATERIALS,
    V2_SAFETY,
    V2_SUMMARY,
    V2_CONFIRM,
)

_CHOICES: dict[str, dict[str, str]] = {
    "incomingKind": {
        "MESSAGE_OR_REQUEST": "сообщение или просьба",
        "COMPLAINT": "обращение с недовольством",
        "FORMAL_DOCUMENT": "документ от пациента",
        "AUTHORITY_OR_COURT_DOCUMENT": "документ суда или органа",
        "OTHER": "другой способ обращения",
        "UNKNOWN": "не удалось определить",
    },
    "incomingSourceStatus": {
        "NOT_ATTACHED": "текст или файл пока не приложен",
        "TEXT_ATTACHED": "добавлен обезличенный текст",
        "FILE_ATTACHED": "добавлен обезличенный файл",
    },
    "situationAreas": {
        "TREATMENT": "лечение",
        "SERVICE": "сервис и взаимодействие с клиникой",
        "MEDICAL_RECORDS": "медицинские документы",
        "PERSONAL_DATA": "персональные данные или изображения",
        "OTHER": "другая тема",
    },
    "conflictStage": {
        "FIRST": "это первое обращение по ситуации",
        "ONGOING": "ситуация продолжается",
        "UNKNOWN": "неизвестно, было ли обращение раньше",
    },
    "clinicActions": {
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
    },
    "healthSignals": {
        "NO_KNOWN_INFORMATION": "сведений об ухудшении здоровья нет",
        "COMPLICATION_OR_WORSENING": "есть сведения об осложнении или ухудшении",
        "OTHER_CLINIC": "есть сведения об обращении в другую клинику",
        "HOSPITALIZATION": "есть сведения о госпитализации",
        "OTHER_CONSEQUENCE": "есть сведения о других последствиях",
        "UNKNOWN": "сведений о последствиях недостаточно",
    },
    "caseMaterialsStatus": {
        "NOT_ATTACHED": "материалы клиники не приложены",
        "ATTACHED": "обезличенные материалы клиники приложены",
    },
}

_MULTI_FIELDS = frozenset({"situationAreas", "clinicActions", "healthSignals"})
_TEXT_FIELDS = {
    "eventSummary": (10, 1_500),
    "clinicActionsNote": (2, 500),
}

_CLARIFICATION_CHOICES: dict[ClarificationQuestionId, tuple[tuple[str, str], ...]] = {
    ClarificationQuestionId.SERVICE_PAYMENT_BASIS: (
        ("PAID", "Платно"),
        ("PUBLICLY_COVERED", "За счёт государственного покрытия (например, ОМС)"),
        ("OTHER", "Другой или смешанный вариант"),
        ("UNKNOWN", "Неизвестно"),
    ),
    ClarificationQuestionId.RESPONSE_PURPOSE: (
        ("RECEIVE_AND_EXPLAIN", "Принять обращение и объяснить ситуацию"),
        ("ANSWER_REQUEST", "Ответить на просьбу или запрос"),
        ("OTHER", "Другая цель"),
        ("UNKNOWN", "Неизвестно"),
    ),
    ClarificationQuestionId.SERVICE_PROVIDED_DATE: (("UNKNOWN", "Дата неизвестна"),),
}


def _clarification_request(request: ClarificationRequest | dict[str, Any]) -> ClarificationRequest:
    # Revalidate model instances too: model_construct/model_copy bypass field validators.
    value = (
        request.model_dump(by_alias=True) if isinstance(request, ClarificationRequest) else request
    )
    return ClarificationRequest.model_validate(value)


def _clarification_service(request: ClarificationRequest, data: dict[str, Any]) -> str | None:
    if request.question_id == ClarificationQuestionId.RESPONSE_PURPOSE:
        return None
    services = data.get("affectedServices")
    slot = request.service_slot
    if (
        not isinstance(services, list)
        or not _valid_services(services)
        or slot is None
        or slot >= len(services)
    ):
        raise ValueError("Уточнение должно относиться к указанной услуге.")
    return str(services[slot])


def clarification_question(
    request: ClarificationRequest | dict[str, Any], data: dict[str, Any],
) -> str:
    """Render only fixed factual wording for a strict, existing service scope."""
    validated = _clarification_request(request)
    service = _clarification_service(validated, data)
    if validated.question_id == ClarificationQuestionId.SERVICE_PAYMENT_BASIS:
        return f"Как оплачивалась услуга «{service}»?"
    if validated.question_id == ClarificationQuestionId.SERVICE_PROVIDED_DATE:
        return (
            f"Когда была оказана услуга «{service}»? Укажите дату услуги, а не дату события: "
            "ГГГГ-ММ-ДД или ДД.ММ.ГГГГ; если дата приблизительная — "
            "«примерно ГГГГ-ММ-ДД»; если неизвестна — «неизвестно»."
        )
    return "Что клинике нужно сделать в связи с обращением?"


def clarification_choices(
    request: ClarificationRequest | dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Closed button values and neutral labels; dates additionally accept typed input."""
    return _CLARIFICATION_CHOICES[_clarification_request(request).question_id]


def _stored_clarification_requests(data: dict[str, Any]) -> list[ClarificationRequest]:
    raw = data.get("analysisClarificationRequests", [])
    if not isinstance(raw, list) or any(
        not isinstance(item, dict) or "questionId" not in item
        or not set(item) <= {"questionId", "serviceSlot"}
        for item in raw
    ):
        return []
    try:
        return ClarificationRequestBatch.model_validate({"requests": raw}).requests
    except ValueError:
        return []


def clarification_requests(data: dict[str, Any]) -> list[ClarificationRequest]:
    """Collect material facts before consent, or preserve a retained recovery batch."""
    if ("analysisClarificationRequests" in data and not _stored_clarification_requests(data)
            and data["analysisClarificationRequests"] != []):
        return []
    try:
        requests = intake_clarification_requests(data)
        for request in requests:
            if request.service_slot is not None and not _needs_services(data):
                return []
            _clarification_service(request, data)
    except ValueError:
        return []
    return requests


def next_clarification_request(data: dict[str, Any]) -> ClarificationRequest | None:
    """UNKNOWN is an explicit attempt; never automatically repeat it."""
    answered = {
        (answer.question_id, answer.service_slot)
        for answer in confirmed_clarification_answers(data).answers
    }
    return next((request for request in clarification_requests(data)
                 if (request.question_id, request.service_slot) not in answered), None)


def previous_clarification_question(data: dict[str, Any]) -> bool:
    """Reopen the preceding requested answer in this draft, retaining other facts."""
    requests = clarification_requests(data)
    pending = next_clarification_request(data)
    preceding = requests[:requests.index(pending)] if pending is not None else requests
    if not preceding:
        return False
    previous = preceding[-1]
    answers = confirmed_clarification_answers(data)
    retained = AnalysisClarificationAnswers(answers=[
        answer for answer in answers.answers
        if (answer.question_id, answer.service_slot) != (
            previous.question_id, previous.service_slot,
        )
    ])
    data["analysisClarificationAnswers"] = retained.model_dump(mode="json", by_alias=True)
    return True


def parse_clarification_answer(
    request: ClarificationRequest | dict[str, Any], raw: str,
) -> ClarificationAnswer:
    """Parse one explicit user answer without assigning legal or event-date meaning."""
    validated = _clarification_request(request)
    if not isinstance(raw, str) or not 1 <= len(raw.strip()) <= 128:
        raise ValueError("Выберите вариант или укажите дату услуги.")
    value: object = raw.strip().upper()
    if validated.question_id == ClarificationQuestionId.SERVICE_PROVIDED_DATE:
        text = raw.strip()
        approximate = text.casefold().startswith("примерно ")
        if approximate:
            text = text[len("примерно "):].strip()
        value = parse_answer("eventDate", text)
        if approximate:
            if not isinstance(value, dict) or value["precision"] != "EXACT":
                raise ValueError("Для приблизительной даты укажите календарную дату услуги.")
            value = {**value, "precision": "APPROXIMATE"}
    return ClarificationAnswer.model_validate({
        **validated.model_dump(by_alias=True), "value": value,
    })


def apply_clarification_answer(
    data: dict[str, Any], request: ClarificationRequest | dict[str, Any], raw: str,
) -> dict[str, Any]:
    """Return a new draft containing one confirmed answer; preserve other identities."""
    validated = _clarification_request(request)
    _clarification_service(validated, data)
    answer = parse_clarification_answer(validated, raw)
    answers = confirmed_clarification_answers(data).answers
    identity = (answer.question_id, answer.service_slot)
    replaced = False
    for index, previous in enumerate(answers):
        if (previous.question_id, previous.service_slot) == identity:
            answers[index] = answer
            replaced = True
            break
    if not replaced:
        answers.append(answer)
    confirmed = AnalysisClarificationAnswers(answers=answers)
    return {
        **data, "analysisClarificationAnswers": confirmed.model_dump(mode="json", by_alias=True),
    }


def invalidate_service_clarification_answers(
    data: dict[str, Any], services: list[str] | None,
) -> dict[str, Any]:
    """Replace/remove services, discarding old scoped answers if the list changed.

    Slots are positions, not stable service identities: even a reordering invalidates
    all service answers. The global response purpose survives a service edit.
    """
    return invalidate_service_scope(data, services)


def clarification_review_blocks(data: dict[str, Any]) -> list[str]:
    """Present confirmed factual answers using their individual service labels."""
    blocks = []
    for answer in confirmed_clarification_answers(data).answers:
        request = ClarificationRequest.model_validate({
            "questionId": answer.question_id, "serviceSlot": answer.service_slot,
        })
        service = _clarification_service(request, data)
        if answer.question_id == ClarificationQuestionId.SERVICE_PROVIDED_DATE:
            value = answer.value
            # The strict answer contract requires ServiceProvidedDate for this ID.
            if isinstance(value, str):
                raise ValueError("invalid service date answer")
            label = "неизвестно" if value.precision == "UNKNOWN" else str(value.provided_date)
            if value.precision == "APPROXIMATE":
                label = "примерно " + label
            blocks.append(f"Дата услуги «{service}»: {label}")
        else:
            label = dict(_CLARIFICATION_CHOICES[answer.question_id])[str(answer.value)]
            prefix = (
                f"Оплата услуги «{service}»"
                if answer.question_id == ClarificationQuestionId.SERVICE_PAYMENT_BASIS
                else "Цель ответа"
            )
            blocks.append(f"{prefix}: {label}")
    return blocks


def choice_items(field: str) -> tuple[tuple[str, str], ...]:
    """Return UI labels for a closed v2 field without exposing mutable storage."""

    try:
        return tuple(_CHOICES[field].items())
    except KeyError as exc:
        raise ValueError("unknown choice field") from exc


def _tokens(raw: str, *, field: str) -> list[str]:
    choices = _CHOICES[field]
    values = [part.strip().upper() for part in raw.split(",") if part.strip()]
    if not values or len(values) > 10 or any(value not in choices for value in values):
        raise ValueError("Выберите вариант кнопкой под текущим вопросом.")
    if len(values) != len(set(values)):
        raise ValueError("Не повторяйте один и тот же вариант.")
    if field == "clinicActions" and "NOTHING_YET" in values and len(values) > 1:
        raise ValueError("«Пока не предпринимали действий» нельзя сочетать с другими действиями.")
    if field == "healthSignals" and (
        ("NO_KNOWN_INFORMATION" in values or "UNKNOWN" in values) and len(values) > 1
    ):
        raise ValueError("Выберите один статус сведений о последствиях.")
    return values


def _safe_text(raw: str, *, field: str) -> str:
    if contains_probable_person_name(raw):
        raise ValueError("Уберите ФИО и другие персональные сведения из ответа.")
    value = pseudonymize_text(raw).text.strip()
    minimum, maximum = _TEXT_FIELDS[field]
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"Введите от {minimum} до {maximum} символов без персональных данных.")
    return value


def _services(raw: str) -> list[str]:
    values = [
        _safe_text(value, field="clinicActionsNote")
        for value in raw.split(";")
        if value.strip()
    ]
    if not 1 <= len(values) <= 5 or len(values) != len(set(values)):
        raise ValueError("Укажите от одной до пяти услуг через точку с запятой.")
    return values


def parse_answer(field: str, raw: str) -> object:
    """Parse only the bounded v2 answer shape; never infer a legal conclusion."""

    if field in _MULTI_FIELDS:
        return _tokens(raw, field=field)
    if field in _CHOICES:
        value = raw.strip().upper()
        if value not in _CHOICES[field]:
            raise ValueError("Выберите вариант кнопкой под текущим вопросом.")
        return value
    if field in _TEXT_FIELDS:
        return _safe_text(raw, field=field)
    if field == "affectedServices":
        return _services(raw)
    if field == "eventDate":
        value = raw.strip()
        if re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", value):
            day, month, year = value.split(".")
            with suppress(ValueError):
                value = date(int(year), int(month), int(day)).isoformat()
        parsed = parse_date_answer(value)
        if parsed is None:
            raise ValueError("Укажите дату ГГГГ-ММ-ДД, ДД.ММ.ГГГГ или «неизвестно».")
        return parsed
    raise ValueError("Неизвестное поле.")


def _valid_enum(value: object, field: str) -> bool:
    return isinstance(value, str) and value in _CHOICES[field]


def _valid_multi(value: object, field: str) -> bool:
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        return False
    if not all(isinstance(item, str) and item in _CHOICES[field] for item in value):
        return False
    try:
        _tokens(",".join(value), field=field)
    except ValueError:
        return False
    return True


def _valid_date(value: object, *, exact_required: bool) -> bool:
    if not isinstance(value, dict) or set(value) != {"date", "precision"}:
        return False
    try:
        parsed = ServiceProvidedDate.model_validate(value)
        if parsed.provided_date is not None and parse_date_answer(parsed.provided_date) is None:
            return False
    except ValueError:
        return False
    return not exact_required or parsed.precision == "EXACT"


def _valid_text(value: object, field: str) -> bool:
    if not isinstance(value, str):
        return False
    minimum, maximum = _TEXT_FIELDS[field]
    return minimum <= len(value.strip()) <= maximum


def _valid_services(value: object) -> bool:
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 5
        and all(isinstance(item, str) and 2 <= len(item.strip()) <= 500 for item in value)
        and len(value) == len(set(value))
    )


def _needs_services(data: dict[str, Any]) -> bool:
    areas = data.get("situationAreas")
    return isinstance(areas, list) and "TREATMENT" in areas


def active_states(data: dict[str, Any]) -> list[str]:
    """Return the fixed conversational order plus the treatment-only service step."""

    states = list(_BASE_STATES)
    if _needs_services(data):
        states.insert(states.index(V2_EVENT), V2_SERVICES)
    if clarification_requests(data) and not known_urgent_report(data):
        states.insert(states.index(V2_SUMMARY), V2_ANALYSIS_CLARIFICATION)
    return states


def _input_complete(data: dict[str, Any], state: str) -> bool:
    if state == V2_INCOMING:
        return _valid_enum(data.get("incomingKind"), "incomingKind") and _valid_enum(
            data.get("incomingSourceStatus"), "incomingSourceStatus"
        )
    if state == V2_SITUATION:
        return _valid_multi(data.get("situationAreas"), "situationAreas")
    if state == V2_SERVICES:
        return _valid_services(data.get("affectedServices"))
    if state == V2_EVENT:
        return _valid_text(data.get("eventSummary"), "eventSummary")
    if state == V2_EVENT_DATE:
        # Collect the remaining facts so a later urgent signal can still be routed.
        # The confirmation and Legal Core analysis boundaries retain the date gate.
        return _valid_date(data.get("eventDate"), exact_required=False)
    if state == V2_CHRONOLOGY:
        # Optional context stays absent; invalid supplied values still need repair.
        return "conflictStage" not in data or _valid_enum(data["conflictStage"], "conflictStage")
    if state == V2_CLINIC_ACTIONS:
        return "clinicActions" not in data or _valid_multi(data["clinicActions"], "clinicActions")
    if state == V2_HEALTH:
        return _valid_multi(data.get("healthSignals"), "healthSignals")
    if state == V2_MATERIALS:
        # This is upload transport state, not a missing fact about the incident.
        # A successful upload writes ATTACHED; absence does not require a question.
        return "caseMaterialsStatus" not in data or _valid_enum(
            data["caseMaterialsStatus"], "caseMaterialsStatus",
        )
    if state == V2_SAFETY:
        return screening_complete(data)
    if state == V2_ANALYSIS_CLARIFICATION:
        return next_clarification_request(data) is None
    return True


def next_missing_state(data: dict[str, Any]) -> str:
    """Return one concrete question; complete input opens the deterministic summary."""

    for state in active_states(data):
        if state in {V2_SUMMARY, V2_CONFIRM}:
            break
        if not _input_complete(data, state):
            return state
    return V2_SUMMARY


def _label(value: object, field: str) -> str:
    if isinstance(value, str):
        return _CHOICES[field].get(value, "не указано")
    if isinstance(value, Iterable):
        labels = [_CHOICES[field].get(str(item), "не указано") for item in value]
        return "; ".join(labels)
    return "не указано"


def _date_label(value: object) -> str:
    if not isinstance(value, dict):
        return "не указано"
    if value.get("precision") == "UNKNOWN":
        return "точная дата пока неизвестна"
    date_value = value.get("date")
    if value.get("precision") == "EXACT" and isinstance(date_value, str):
        return date_value
    if value.get("precision") == "APPROXIMATE" and _valid_date(value, exact_required=False):
        return "примерно " + str(date_value)
    return "не указано"


def review_blocks(data: dict[str, Any], *, final: bool = False) -> list[str]:
    """Render a factual recap with no legal label, calculation or recommendation."""

    title = (
        "📋 Краткая фабула — подтвердите сведения"
        if final
        else "🧩 Проверьте, верно ли понята ситуация"
    )
    blocks = [title]
    blocks.append("Что поступило: " + _label(data.get("incomingKind"), "incomingKind"))
    blocks.append("Связано с: " + _label(data.get("situationAreas"), "situationAreas"))
    if _needs_services(data):
        services = data.get("affectedServices")
        blocks.append(
            "Услуги: " + "; ".join(services)
            if isinstance(services, list)
            else "Услуги: не указаны"
        )
    summary = data.get("eventSummary")
    blocks.append(
        "Что произошло: " + summary
        if isinstance(summary, str)
        else "Что произошло: не указано"
    )
    blocks.append("Когда: " + _date_label(data.get("eventDate")))
    blocks.append("Ход ситуации: " + _label(data.get("conflictStage"), "conflictStage"))
    blocks.append("Что сделала клиника: " + _label(data.get("clinicActions"), "clinicActions"))
    blocks.append(
        "Последствия для здоровья: " + _label(data.get("healthSignals"), "healthSignals")
    )
    blocks.append(
        "Материалы клиники: "
        + _label(data.get("caseMaterialsStatus", "NOT_ATTACHED"), "caseMaterialsStatus")
    )
    blocks.extend(screening_summary(data))
    blocks.extend(clarification_review_blocks(data))
    if data.get("eventDate") == {"date": None, "precision": "UNKNOWN"}:
        blocks.append(
            "Чтобы оценить ситуацию по датам, потребуется уточнить хотя бы ориентир времени."
        )
    blocks.append(
        "Это изложение фактов, а не оценка нарушения, срока или ответственности. "
        "Неуказанные сведения не считаются отсутствующими."
    )
    return blocks


def review_pages(data: dict[str, Any], *, limit: int = 3900) -> list[str]:
    """Retain the whole factual summary within Telegram's UTF-16 message bound."""
    pages: list[str] = []
    current: list[str] = []
    units = 0
    for block in review_blocks(data, final=True):
        block_units = sum(2 if ord(character) > 0xFFFF else 1 for character in block)
        if current and units + block_units + 2 > limit:
            pages.append("".join(current))
            current, units = [], 0
        separator = "\n\n" if current else ""
        for character in separator + block:
            width = 2 if ord(character) > 0xFFFF else 1
            if units + width > limit:
                pages.append("".join(current))
                current, units = [], 0
            current.append(character)
            units += width
    if current:
        pages.append("".join(current))
    return pages
