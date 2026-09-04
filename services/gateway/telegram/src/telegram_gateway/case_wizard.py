"""Validated Case Core client and deterministic Telegram intake mapping."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, TypeAlias
from uuid import UUID, uuid4

import httpx2

INTAKE_SCHEMA_VERSION = "dental-case-intake.v1"
MAX_PDF_BYTES = 8 * 1024 * 1024
DateFactValue: TypeAlias = dict[str, str | None]
SignalAnswer: TypeAlias = bool | Literal["YES", "NO", "UNKNOWN"]
_UNKNOWN_DATE_ANSWERS = frozenset({"неизвестно", "не известно", "не знаю", "unknown"})


class LegalCoreApiError(Exception):
    """Safe error raised at the trusted Legal Core boundary."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True, slots=True)
class CaseDraft:
    incident_type: str
    service_type: str
    service_date: str | DateFactValue
    incident_date: str | DateFactValue
    claim_date: str | DateFactValue
    problem_summary: str
    patient_demand: str
    formal_claim: SignalAnswer
    harm_claimed: SignalAnswer
    regulator_or_court: SignalAnswer
    documents_status: str
    demand_amount_kopecks: int | None = None
    claim_received_at: str | DateFactValue | None = None
    response_deadline: str | DateFactValue | None = None
    hospitalization: SignalAnswer | None = None
    authority_kind: str | None = None
    authority_document_date: str | DateFactValue | None = None
    lawyer_contact: SignalAnswer = False
    representative_authority: SignalAnswer | None = None
    regulator_threat: SignalAnswer = False
    primary_incident_type: str | None = None


def parse_iso_date(
    value: str, *, today: str | None = None, allow_future: bool = False
) -> str | None:
    """Accept a real ISO date that is not in the future."""

    try:
        parsed = date.fromisoformat(value.strip())
        upper_bound = date.today() if today is None else date.fromisoformat(today)
    except ValueError:
        return None
    return parsed.isoformat() if allow_future or parsed <= upper_bound else None


def parse_date_answer(
    value: str, *, today: str | None = None, allow_future: bool = False
) -> DateFactValue | None:
    """Return an exact date or preserve an administrator's explicit unknown answer."""

    if value.strip().casefold() in _UNKNOWN_DATE_ANSWERS:
        return {"date": None, "precision": "UNKNOWN"}
    parsed = parse_iso_date(value, today=today, allow_future=allow_future)
    if parsed is None:
        return None
    return {"date": parsed, "precision": "EXACT"}


def parse_ruble_amount_to_kopecks(value: str) -> int | None:
    """Parse at most two decimal places and return a bounded integer number of kopecks."""

    normalized = value.strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    if not amount.is_finite() or not Decimal("0.01") <= amount <= Decimal("1000000000"):
        return None
    try:
        rounded = amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    if rounded != amount:
        return None
    return int(rounded * 100)


def _fact(key: str, value_type: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "factKey": key,
        "valueType": value_type,
        "value": value,
        "sourceType": "USER_STATEMENT",
    }


def _date_fact(value: str | DateFactValue) -> DateFactValue:
    if isinstance(value, str):
        return {"date": value, "precision": "EXACT"}
    if set(value) != {"date", "precision"}:
        raise ValueError("unsupported date value")
    date_value = value["date"]
    precision = value["precision"]
    if precision == "UNKNOWN" and date_value is None:
        return {"date": None, "precision": "UNKNOWN"}
    if precision in {"EXACT", "APPROXIMATE"} and isinstance(date_value, str):
        return {"date": date_value, "precision": precision}
    raise ValueError("unsupported date value")


def _signal_fact(key: str, value: SignalAnswer) -> dict[str, Any]:
    if isinstance(value, bool):
        return _fact(key, "BOOLEAN", {"boolean": value})
    if value in {"YES", "NO", "UNKNOWN"}:
        return _fact(key, "BOOLEAN", {"state": value})
    raise ValueError("unsupported signal answer")


def _is_yes(value: SignalAnswer) -> bool:
    return value is True or value == "YES"


def _requires_hospitalization(value: SignalAnswer) -> bool:
    return _is_yes(value) or value == "UNKNOWN"


def facts_from_draft(draft: CaseDraft) -> list[dict[str, Any]]:
    """Map the pseudonymous wizard draft to the versioned Legal Core contract."""

    documents = {
        "COMPLETE": {
            "CONTRACT": "AVAILABLE",
            "MEDICAL_RECORD": "AVAILABLE",
            "INFORMED_CONSENT": "AVAILABLE",
        },
        "PARTIAL": {
            "CONTRACT": "UNKNOWN",
            "MEDICAL_RECORD": "UNKNOWN",
            "INFORMED_CONSENT": "UNKNOWN",
        },
        "NONE": {
            "CONTRACT": "MISSING",
            "MEDICAL_RECORD": "MISSING",
            "INFORMED_CONSENT": "MISSING",
        },
    }
    if draft.documents_status not in documents:
        raise ValueError("unsupported documents status")

    facts = [
        _fact("INCIDENT_TYPES", "ENUM_SET", {"values": [draft.incident_type]}),
        _fact(
            "PRIMARY_INCIDENT_TYPE",
            "ENUM",
            {"value": draft.primary_incident_type or draft.incident_type},
        ),
        _fact("SERVICE_TYPE", "TEXT", {"text": draft.service_type}),
        _fact("SERVICE_DATE", "DATE", _date_fact(draft.service_date)),
        _fact("INCIDENT_DATE", "DATE", _date_fact(draft.incident_date)),
        _fact("CLAIM_DATE", "DATE", _date_fact(draft.claim_date)),
        _fact("PROBLEM_SUMMARY", "TEXT", {"text": draft.problem_summary}),
        _fact("PATIENT_DEMAND", "ENUM_SET", {"values": [draft.patient_demand]}),
        _signal_fact("FORMAL_CLAIM", draft.formal_claim),
        _signal_fact("HARM_CLAIMED", draft.harm_claimed),
        _signal_fact("LAWYER_CONTACT", draft.lawyer_contact),
        _signal_fact("REGULATOR_OR_COURT", draft.regulator_or_court),
        _signal_fact("REGULATOR_THREAT", draft.regulator_threat),
        _fact("CLINIC_DOCUMENTS", "DOCUMENT_INVENTORY", documents[draft.documents_status]),
    ]
    if draft.demand_amount_kopecks is not None:
        facts.append(
            _fact(
                "DEMAND_AMOUNT",
                "MONEY",
                {"amountKopecks": draft.demand_amount_kopecks, "currency": "RUB"},
            )
        )
    if _is_yes(draft.formal_claim):
        if draft.claim_received_at is None or draft.response_deadline is None:
            raise ValueError("formal claim dates are required")
        facts.extend(
            [
                _fact(
                    "CLAIM_RECEIVED_AT",
                    "DATE",
                    _date_fact(draft.claim_received_at),
                ),
                _fact(
                    "RESPONSE_DEADLINE",
                    "DATE",
                    _date_fact(draft.response_deadline),
                ),
            ]
        )
    if _requires_hospitalization(draft.harm_claimed):
        if draft.hospitalization is None:
            raise ValueError("hospitalization status is required")
        facts.append(_signal_fact("HOSPITALIZATION", draft.hospitalization))
    if _is_yes(draft.lawyer_contact):
        if draft.representative_authority is None or draft.response_deadline is None:
            raise ValueError("lawyer contact details are required")
        facts.append(
            _signal_fact("REPRESENTATIVE_AUTHORITY", draft.representative_authority)
        )
    if _is_yes(draft.regulator_or_court):
        if (
            draft.authority_kind is None
            or draft.authority_document_date is None
            or draft.response_deadline is None
        ):
            raise ValueError("authority details are required")
        facts.extend(
            [
                _fact("AUTHORITY_KIND", "TEXT", {"text": draft.authority_kind}),
                _fact(
                    "DOCUMENT_DATE",
                    "DATE",
                    _date_fact(draft.authority_document_date),
                ),
            ]
        )
        if not _is_yes(draft.formal_claim):
            facts.append(
                _fact(
                    "RESPONSE_DEADLINE",
                    "DATE",
                    _date_fact(draft.response_deadline),
                )
            )
    return facts


def telegram_summary_from_report(report: dict[str, Any]) -> str:
    """Render the Telegram card only from a validated canonical report response."""

    try:
        if report.get("schemaVersion") != "dental-case-report.v1":
            raise ValueError
        case = report["case"]
        summary = report["summary"]
        public_number = case["publicNumber"]
        status = case["status"]
        neutral_description = summary["neutralDescription"]
        missing_facts = report["missingFacts"]
        recommendations_status = report["recommendations"]["status"]
        draft_status = report["draftResponse"]["status"]
        legal_status = report["legalBasis"]["status"]
        disclaimer = report["disclaimer"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid canonical report") from exc

    scalar_values = (
        public_number,
        status,
        neutral_description,
        recommendations_status,
        draft_status,
        legal_status,
        disclaimer,
    )
    if not all(isinstance(value, str) for value in scalar_values):
        raise ValueError("invalid canonical report")
    if not isinstance(missing_facts, list) or len(missing_facts) > 30:
        raise ValueError("invalid canonical report")
    if len(neutral_description) > 1_500 or len(disclaimer) > 500:
        raise ValueError("invalid canonical report")

    missing_count = len(missing_facts)
    analysis_label = (
        "АНАЛИЗ ЗАБЛОКИРОВАН"
        if {recommendations_status, draft_status, legal_status} == {"NOT_AVAILABLE"}
        else "ТРЕБУЕТ ПРОВЕРКИ"
    )
    rendered = (
        f"📋 {public_number}\n"
        f"Статус: {status}\n\n"
        f"Что произошло:\n{neutral_description}\n\n"
        f"Недостающих фактов: {missing_count}\n"
        f"⚖️ {analysis_label}\n\n"
        f"{disclaimer}"
    )
    if len(rendered) > 4_000:
        raise ValueError("invalid canonical report")
    return rendered


class LegalCoreClient:
    """Small typed facade around one application-scoped ``AsyncClient``."""

    def __init__(self, http: httpx2.AsyncClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _headers(telegram_user_id: int, idempotency_key: UUID | None = None) -> dict[str, str]:
        headers = {"X-Telegram-User-Id": str(telegram_user_id)}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = str(idempotency_key)
        return headers

    async def _json_request(
        self,
        method: str,
        path: str,
        *,
        telegram_user_id: int,
        payload: dict[str, Any] | None = None,
        idempotency_key: UUID | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method,
                path,
                headers=self._headers(telegram_user_id, idempotency_key),
                json=payload,
            )
        except httpx2.HTTPError as exc:
            raise LegalCoreApiError(
                503, "LEGAL_CORE_UNAVAILABLE", "Legal Core unavailable"
            ) from exc
        if response.status_code >= 400:
            code = "LEGAL_CORE_ERROR"
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("error"), dict):
                    candidate = body["error"].get("code")
                    if isinstance(candidate, str) and candidate:
                        code = candidate
            except ValueError:
                pass
            raise LegalCoreApiError(response.status_code, code, "Legal Core rejected request")
        try:
            body = response.json()
        except ValueError as exc:
            raise LegalCoreApiError(502, "INVALID_LEGAL_CORE_RESPONSE", "Invalid response") from exc
        if not isinstance(body, dict):
            raise LegalCoreApiError(502, "INVALID_LEGAL_CORE_RESPONSE", "Invalid response")
        return body

    async def get_actor(self, telegram_user_id: int) -> dict[str, Any]:
        return await self._json_request(
            "GET",
            "/v1/actor",
            telegram_user_id=telegram_user_id,
        )

    async def list_clinic_members(self, telegram_user_id: int) -> dict[str, Any]:
        return await self._json_request(
            "GET", "/v1/clinic/members", telegram_user_id=telegram_user_id
        )

    async def add_clinic_member(
        self, telegram_user_id: int, target_telegram_user_id: int, *, role: str
    ) -> dict[str, Any]:
        return await self._json_request(
            "POST",
            "/v1/clinic/members",
            telegram_user_id=telegram_user_id,
            payload={"telegramUserId": target_telegram_user_id, "role": role},
        )

    async def grant_subscription(
        self,
        telegram_user_id: int,
        target_telegram_user_id: int,
        *,
        plan_code: str = "MVP_MANUAL",
        pilot_days: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "telegramUserId": target_telegram_user_id,
            "planCode": plan_code,
        }
        if pilot_days is not None:
            payload["pilotDays"] = pilot_days
        return await self._json_request(
            "POST",
            "/v1/platform/subscription-grants",
            telegram_user_id=telegram_user_id,
            idempotency_key=uuid4(),
            payload=payload,
        )

    async def create_intake_draft(self, telegram_user_id: int) -> dict[str, Any]:
        return await self._json_request(
            "POST",
            "/v1/telegram-intake-drafts",
            telegram_user_id=telegram_user_id,
            idempotency_key=uuid4(),
            payload={},
        )

    async def list_intake_drafts(self, telegram_user_id: int) -> dict[str, Any]:
        return await self._json_request(
            "GET", "/v1/telegram-intake-drafts", telegram_user_id=telegram_user_id
        )

    async def get_intake_draft(
        self, draft_id: UUID, telegram_user_id: int
    ) -> dict[str, Any]:
        return await self._json_request(
            "GET", f"/v1/telegram-intake-drafts/{draft_id}", telegram_user_id=telegram_user_id
        )

    async def save_intake_draft(
        self,
        draft_id: UUID,
        telegram_user_id: int,
        *,
        expected_revision: int,
        wizard_state: str,
        draft_data: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._json_request(
            "PUT",
            f"/v1/telegram-intake-drafts/{draft_id}",
            telegram_user_id=telegram_user_id,
            idempotency_key=uuid4(),
            payload={
                "expectedRevision": expected_revision,
                "wizardState": wizard_state,
                "draftData": draft_data,
            },
        )

    async def archive_intake_draft(
        self, draft_id: UUID, telegram_user_id: int, *, expected_revision: int
    ) -> dict[str, Any]:
        return await self._json_request(
            "POST",
            f"/v1/telegram-intake-drafts/{draft_id}/archive",
            telegram_user_id=telegram_user_id,
            idempotency_key=uuid4(),
            payload={"expectedRevision": expected_revision},
        )

    async def submit_workflow(
        self,
        workflow_id: UUID,
        facts: list[dict[str, Any]],
        telegram_user_id: int,
    ) -> dict[str, Any]:
        return await self._json_request(
            "POST",
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            telegram_user_id=telegram_user_id,
            payload={
                "intakeSchemaVersion": INTAKE_SCHEMA_VERSION,
                "locale": "ru-RU",
                "facts": facts,
            },
        )

    async def get_workflow(
        self, workflow_id: UUID, telegram_user_id: int
    ) -> dict[str, Any]:
        return await self._json_request(
            "GET",
            f"/v1/telegram-case-workflows/{workflow_id}",
            telegram_user_id=telegram_user_id,
        )

    async def download_pdf(self, report_id: UUID, telegram_user_id: int) -> bytes:
        try:
            response = await self._http.get(
                f"/v1/reports/{report_id}/pdf",
                headers=self._headers(telegram_user_id),
            )
        except httpx2.HTTPError as exc:
            raise LegalCoreApiError(
                503, "LEGAL_CORE_UNAVAILABLE", "Legal Core unavailable"
            ) from exc
        if response.status_code >= 400:
            raise LegalCoreApiError(response.status_code, "PDF_NOT_AVAILABLE", "PDF unavailable")
        content = response.content
        if (
            response.headers.get("content-type", "").split(";", 1)[0] != "application/pdf"
            or not content.startswith(b"%PDF-")
            or len(content) > MAX_PDF_BYTES
        ):
            raise LegalCoreApiError(502, "INVALID_PDF_RESPONSE", "Invalid PDF response")
        return content
