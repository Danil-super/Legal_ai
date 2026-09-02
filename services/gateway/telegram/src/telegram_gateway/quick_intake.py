"""Local, non-LLM candidate extraction for a one-message dental incident description.

The extractor is deliberately conservative. It never produces legal conclusions and never treats
absence of a phrase as a negative fact. Only a contiguous prefix of the normal Telegram wizard is
eligible for automatic draft prefill; later extracted hints are shown to the administrator but are
not persisted until the missing earlier fields have been collected by the existing wizard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from legal_core.pseudonymization import pseudonymize_text

_MIN_DESCRIPTION = 10
_MAX_DESCRIPTION = 1500
_NAME_WORD = r"[А-ЯЁ][а-яё]{1,30}(?:-[А-ЯЁ][а-яё]{1,30})?"
_LABELED_NAME: Final = re.compile(
    rf"\b(?i:фио|пациент(?:а|у|ом)?|врач(?:а|у|ом)?|доктор(?:а|у|ом)?|"
    rf"представител(?:ь|я|ю|ем))\s*[:—-]?\s+{_NAME_WORD}\s+{_NAME_WORD}"
)
_FULL_NAME: Final = re.compile(
    rf"(?<![А-ЯЁа-яё]){_NAME_WORD}\s+{_NAME_WORD}\s+{_NAME_WORD}(?![А-ЯЁа-яё])"
)
_INITIALS_NAME: Final = re.compile(
    rf"(?:{_NAME_WORD}\s+[А-ЯЁ]\.[А-ЯЁ]\.|[А-ЯЁ]\.[А-ЯЁ]\.\s+{_NAME_WORD})"
)
_DATE: Final = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})(?!\d)")
_NUMBER = r"(\d{1,3}(?:[\s\u00a0]\d{3})+|\d+(?:[.,]\d{1,2})?)"
_MULTIPLIER = r"(тыс(?:яч[аи]?)?\.?|млн\.?|миллион(?:а|ов)?)"
_CURRENCY = r"(?:₽|руб(?:л(?:ей|я|ь)?)?\.?)"
_MONEY_WITH_MULTIPLIER: Final = re.compile(
    rf"(?<!\d){_NUMBER}\s*{_MULTIPLIER}(?:\s*{_CURRENCY})?",
    re.IGNORECASE,
)
_MONEY_WITH_CURRENCY: Final = re.compile(
    rf"(?<!\d){_NUMBER}\s*{_CURRENCY}",
    re.IGNORECASE,
)
_RETURN_MONEY: Final = re.compile(
    r"\bвернуть\s+(?:(?:денежные\s+)?средства|деньги|сумму\s+|\d)",
    re.IGNORECASE,
)

_INCIDENT_QUALITY = (
    "скол",
    "откол",
    "слом",
    "трещин",
    "выпал",
    "корон",
    "винир",
    "пломб",
    "имплант",
    "протез",
    "реставрац",
    "боль",
    "осложнен",
    "отек",
    "отёк",
    "онемен",
    "качество леч",
    "некачествен",
)
_SERVICE_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("имплант",), "имплантация зуба"),
    (("винир",), "установка винира"),
    (("корон",), "установка коронки"),
    (("пломб", "реставрац"), "терапевтическая реставрация зуба"),
    (("удален", "удалён", "экстракц"), "удаление зуба"),
    (("протез",), "ортопедическое протезирование"),
    (("брекет", "ортодонт"), "ортодонтическое лечение"),
    (("эндодонт", "канал"), "эндодонтическое лечение"),
    (("гигиен", "чистк"), "профессиональная гигиена"),
    (("отбел",), "отбеливание зубов"),
)


class QuickIntakeError(ValueError):
    pass


class QuickIntakePrivacyError(QuickIntakeError):
    pass


@dataclass(frozen=True, slots=True)
class QuickIntakeResult:
    sanitized_text: str
    candidate_data: dict[str, object]
    draft_data: dict[str, object]
    next_wizard_state: str
    dropped_candidate_fields: tuple[str, ...]
    redaction_counts: dict[str, int]


def contains_probable_person_name(text: str) -> bool:
    """Conservatively reject likely Russian FIO before persistent quick-intake storage."""

    patterns = (_LABELED_NAME, _FULL_NAME, _INITIALS_NAME)
    return any(pattern.search(text) is not None for pattern in patterns)


def _normalize_date(raw: str, *, today: date) -> str | None:
    try:
        parsed = (
            date.fromisoformat(raw)
            if "-" in raw
            else datetime.strptime(raw, "%d.%m.%Y").date()
        )
    except ValueError:
        return None
    if parsed > today:
        return None
    return parsed.isoformat()


def _date_context_score(context: str, keywords: tuple[str, ...]) -> int:
    folded = context.casefold()
    return sum(1 for keyword in keywords if keyword in folded)


def _sentence_context(text: str, *, start: int, end: int) -> str:
    """Return the closest sentence/clause around a date instead of a broad overlapping window."""

    separators = ".;!?\n"
    left = start
    while left > 0 and text[left - 1] not in separators:
        left -= 1
    right = end
    while right < len(text) and text[right] not in separators:
        right += 1
    return text[left:right].strip()


def _extract_labeled_dates(text: str, *, today: date) -> dict[str, dict[str, str]]:
    assignments: dict[str, dict[str, str]] = {}
    categories = {
        "service_date": (
            "дата леч",
            "дата услуг",
            "дата установ",
            "установили",
            "поставили",
            "провели леч",
            "лечили",
            "удалили",
        ),
        "incident_date": (
            "дата проблем",
            "проблема",
            "скол",
            "слом",
            "откол",
            "боль",
            "осложнен",
            "выпал",
            "появил",
        ),
        "claim_date": (
            "дата обращ",
            "обратил",
            "претенз",
            "потреб",
            "требу",
            "жалоб",
            "написал",
            "позвонил",
        ),
    }
    for match in _DATE.finditer(text):
        normalized = _normalize_date(match.group(1), today=today)
        if normalized is None:
            continue
        context = _sentence_context(text, start=match.start(), end=match.end())
        scores = {
            field: _date_context_score(context, keywords)
            for field, keywords in categories.items()
        }
        best_score = max(scores.values(), default=0)
        if best_score == 0:
            continue
        winners = [field for field, score in scores.items() if score == best_score]
        if len(winners) != 1 or winners[0] in assignments:
            continue
        assignments[winners[0]] = {"date": normalized, "precision": "EXACT"}
    return assignments


def _incident_type(folded: str) -> str:
    if any(token in folded for token in ("персональн", "врачебн", "утечк", "разглаш")):
        return "PERSONAL_DATA"
    if any(token in folded for token in _INCIDENT_QUALITY):
        return "QUALITY_COMPLAINT"
    if any(
        token in folded
        for token in ("идс", "информированн", "согласие", "медицинск документ")
    ):
        return "INFORMED_CONSENT"
    if any(token in folded for token in ("возврат", "вернуть деньги", "оплат", "счет", "счёт")):
        return "PAYMENT_DISPUTE"
    return "OTHER"


def _service_type(folded: str) -> str | None:
    for markers, value in _SERVICE_MAP:
        if any(marker in folded for marker in markers):
            return value
    return None


def _demand(text: str, folded: str) -> str | None:
    matched: list[str] = []
    if any(token in folded for token in ("компенсац", "моральн", "возместить ущерб")):
        matched.append("COMPENSATION_DEMAND")
    if any(token in folded for token in ("возврат", "вернуть деньги", "деньги обратно")) or (
        _RETURN_MONEY.search(text) is not None
    ):
        matched.append("REFUND_DEMAND")
    if any(
        token in folded
        for token in ("передел", "заменить бесплатно", "повторное леч", "исправить бесплатно")
    ):
        matched.append("REWORK_DEMAND")
    if any(
        token in folded
        for token in ("ничего не требует", "требований нет", "требований пока нет")
    ):
        matched.append("NO_SPECIFIC_DEMAND")
    unique = list(dict.fromkeys(matched))
    return unique[0] if len(unique) == 1 else None


def _money_value(number_raw: str, multiplier_raw: str | None) -> int | None:
    raw = number_raw.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        return None
    multiplier = 1
    normalized_multiplier = (multiplier_raw or "").casefold()
    if normalized_multiplier.startswith("тыс"):
        multiplier = 1_000
    elif normalized_multiplier.startswith("млн") or normalized_multiplier.startswith("миллион"):
        multiplier = 1_000_000
    rubles = amount * multiplier
    if not 100 <= rubles <= 1_000_000_000:
        return None
    kopecks = round(rubles * 100)
    return kopecks if abs(kopecks / 100 - rubles) < 0.001 else None


def _money_kopecks(text: str, folded: str) -> int | None:
    demand_context = any(
        token in folded
        for token in ("треб", "возврат", "компенсац", "вернуть", "деньги", "сумм")
    )
    if not demand_context:
        return None

    candidates: list[int] = []
    occupied: list[tuple[int, int]] = []
    for match in _MONEY_WITH_MULTIPLIER.finditer(text):
        value = _money_value(match.group(1), match.group(2))
        if value is not None:
            candidates.append(value)
            occupied.append(match.span())

    for match in _MONEY_WITH_CURRENCY.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in occupied):
            continue
        value = _money_value(match.group(1), None)
        if value is not None:
            candidates.append(value)

    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else None


def _explicit_signal(
    folded: str,
    *,
    positive: tuple[str, ...],
    negative: tuple[str, ...],
) -> str | None:
    if any(token in folded for token in negative):
        return "NO"
    if any(token in folded for token in positive):
        return "YES"
    return None


def _formal_claim(folded: str) -> str | None:
    return _explicit_signal(
        folded,
        positive=(
            "письменная претенз",
            "письменную претенз",
            "прислал претенз",
            "направил претенз",
            "получили претенз",
            "поступила претенз",
        ),
        negative=(
            "письменной претензии нет",
            "письменная претензия не поступала",
            "претензия не поступала",
        ),
    )


def _harm_claim(folded: str) -> str | None:
    return _explicit_signal(
        folded,
        positive=(
            "вред здоров",
            "ущерб здоров",
            "причинен вред",
            "причинён вред",
            "заявляет о вред",
        ),
        negative=("о вреде здоровью не заяв", "вред здоровью не заяв"),
    )


def _lawyer_contact(folded: str) -> str | None:
    positive = (
        "письмо от юриста",
        "юрист пациента написал",
        "юрист пациента связ",
        "адвокат пациента",
        "представитель пациента написал",
        "представитель пациента связ",
    )
    negative = (
        "юрист не обращался",
        "представитель не обращался",
        "адвокат не обращался",
    )
    return _explicit_signal(folded, positive=positive, negative=negative)


def _regulator_signals(folded: str) -> tuple[str | None, str | None]:
    authority_tokens = ("роспотреб", "росздрав", "прокурат", "суд", "надзор")
    if not any(token in folded for token in authority_tokens):
        return None, None
    threat = any(
        token in folded
        for token in (
            "угрож",
            "собирается обратиться",
            "собирается подать",
            "говорит что пойдет",
            "говорит, что пойдет",
            "пойдёт в",
            "пойдет в",
            "напишет жалоб",
        )
    )
    actual = any(
        token in folded
        for token in (
            "подал в суд",
            "подала в суд",
            "иск подан",
            "исковое заяв",
            "получил запрос",
            "получила запрос",
            "получен запрос",
            "поступил запрос",
            "пришел запрос",
            "пришёл запрос",
            "получили предпис",
            "началась проверк",
            "пришла проверк",
            "обратился в роспотреб",
            "обратилась в роспотреб",
            "обратился в росздрав",
            "обратилась в росздрав",
        )
    )
    return ("YES" if actual else None, "YES" if threat else None)


def _documents_status(folded: str) -> str | None:
    if any(
        token in folded
        for token in ("документов нет", "нет договора и карты", "договор и идс отсутств")
    ):
        return "NONE"
    if any(
        token in folded
        for token in ("есть не все документы", "есть не всё", "часть документов")
    ):
        return "PARTIAL"
    complete_markers = (
        "договор, карта и идс есть",
        "договор, медкарта и идс есть",
        "все основные документы есть",
        "все документы есть",
    )
    if any(token in folded for token in complete_markers):
        return "COMPLETE"
    return None


def _next_state(data: dict[str, object]) -> str:
    for field, state in (
        ("incident_type", "INCIDENT"),
        ("service_type", "SERVICE_TYPE"),
        ("service_date", "SERVICE_DATE"),
        ("incident_date", "INCIDENT_DATE"),
        ("claim_date", "CLAIM_DATE"),
        ("problem_summary", "PROBLEM_SUMMARY"),
        ("patient_demand", "PATIENT_DEMAND"),
    ):
        if field not in data:
            return state
    if (
        data["patient_demand"] in {"REFUND_DEMAND", "COMPENSATION_DEMAND"}
        and "demand_amount_kopecks" not in data
    ):
        return "DEMAND_AMOUNT"
    if "formal_claim" not in data:
        return "FORMAL_CLAIM"
    if data["formal_claim"] == "YES":
        return "CLAIM_RECEIVED_AT"
    if "harm_claimed" not in data:
        return "HARM"
    if data["harm_claimed"] in {"YES", "UNKNOWN"} and "hospitalization" not in data:
        return "HOSPITALIZATION"
    if "lawyer_contact" not in data:
        return "LAWYER"
    if data["lawyer_contact"] == "YES":
        return "REPRESENTATIVE_AUTHORITY"
    if "regulator_or_court" not in data:
        return "AUTHORITY"
    if data["regulator_or_court"] == "YES":
        return "AUTHORITY_KIND"
    if "regulator_threat" not in data:
        return "REGULATOR_THREAT"
    if "documents_status" not in data:
        return "DOCUMENTS"
    return "CONFIRM"


def _prefix_fields_for_state(state: str, data: dict[str, object]) -> set[str]:
    ordered = [
        ("incident_type", "INCIDENT"),
        ("service_type", "SERVICE_TYPE"),
        ("service_date", "SERVICE_DATE"),
        ("incident_date", "INCIDENT_DATE"),
        ("claim_date", "CLAIM_DATE"),
        ("problem_summary", "PROBLEM_SUMMARY"),
        ("patient_demand", "PATIENT_DEMAND"),
    ]
    allowed: set[str] = set()
    for field, field_state in ordered:
        if field_state == state:
            return allowed
        if field in data:
            allowed.add(field)
    if state == "DEMAND_AMOUNT":
        return allowed
    if "demand_amount_kopecks" in data:
        allowed.add("demand_amount_kopecks")
    if state == "FORMAL_CLAIM":
        return allowed
    if "formal_claim" in data:
        allowed.add("formal_claim")
    if data.get("formal_claim") == "YES" or state in {
        "CLAIM_RECEIVED_AT",
        "CLAIM_DEADLINE",
    }:
        return allowed
    if state == "HARM":
        return allowed
    if "harm_claimed" in data:
        allowed.add("harm_claimed")
    if state == "HOSPITALIZATION":
        return allowed
    if "hospitalization" in data:
        allowed.add("hospitalization")
    if state == "LAWYER":
        return allowed
    if "lawyer_contact" in data:
        allowed.add("lawyer_contact")
    if data.get("lawyer_contact") == "YES" or state in {
        "REPRESENTATIVE_AUTHORITY",
        "LAWYER_DEADLINE",
    }:
        return allowed
    if state == "AUTHORITY":
        return allowed
    if "regulator_or_court" in data:
        allowed.add("regulator_or_court")
    if data.get("regulator_or_court") == "YES" or state in {
        "AUTHORITY_KIND",
        "AUTHORITY_DATE",
        "AUTHORITY_DEADLINE",
    }:
        return allowed
    if state == "REGULATOR_THREAT":
        return allowed
    if "regulator_threat" in data:
        allowed.add("regulator_threat")
    if state == "DOCUMENTS":
        return allowed
    if "documents_status" in data:
        allowed.add("documents_status")
    return allowed


def extract_quick_intake(text: str, *, today: date | None = None) -> QuickIntakeResult:
    raw = text.strip()
    if not _MIN_DESCRIPTION <= len(raw) <= _MAX_DESCRIPTION:
        raise QuickIntakeError("description must contain 10-1500 characters")
    if contains_probable_person_name(raw):
        raise QuickIntakePrivacyError("remove patient/staff names before quick intake")

    pseudonymized = pseudonymize_text(raw)
    sanitized = pseudonymized.text
    folded = sanitized.casefold()
    current_day = today or date.today()

    candidates: dict[str, object] = {
        "incident_type": _incident_type(folded),
        "problem_summary": sanitized,
    }
    service = _service_type(folded)
    if service is not None:
        candidates["service_type"] = service
    candidates.update(_extract_labeled_dates(sanitized, today=current_day))

    demand = _demand(sanitized, folded)
    if demand is not None:
        candidates["patient_demand"] = demand
        if demand in {"REFUND_DEMAND", "COMPENSATION_DEMAND"}:
            amount = _money_kopecks(sanitized, folded)
            if amount is not None:
                candidates["demand_amount_kopecks"] = amount

    formal = _formal_claim(folded)
    if formal is not None:
        candidates["formal_claim"] = formal
    harm = _harm_claim(folded)
    if harm is not None:
        candidates["harm_claimed"] = harm
    if "госпитализ" in folded:
        candidates["hospitalization"] = "YES"
    lawyer = _lawyer_contact(folded)
    if lawyer is not None:
        candidates["lawyer_contact"] = lawyer
    actual_authority, threat = _regulator_signals(folded)
    if actual_authority is not None:
        candidates["regulator_or_court"] = actual_authority
    if threat is not None:
        candidates["regulator_threat"] = threat
    documents = _documents_status(folded)
    if documents is not None:
        candidates["documents_status"] = documents

    next_state = _next_state(candidates)
    allowed_prefix = _prefix_fields_for_state(next_state, candidates)
    draft_data = {key: value for key, value in candidates.items() if key in allowed_prefix}
    dropped = tuple(sorted(set(candidates) - set(draft_data)))
    return QuickIntakeResult(
        sanitized_text=sanitized,
        candidate_data=candidates,
        draft_data=draft_data,
        next_wizard_state=next_state,
        dropped_candidate_fields=dropped,
        redaction_counts=pseudonymized.replacement_counts,
    )
