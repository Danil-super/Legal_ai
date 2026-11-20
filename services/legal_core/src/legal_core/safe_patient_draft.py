# ruff: noqa: RUF001
"""Deterministic, non-legal patient-facing draft for verified low/medium risk cases.

This is intentionally not an LLM writer. It never echoes free-text case facts, never states fault,
causation or a legal entitlement, and never promises a refund or outcome. The result remains a draft
that requires a human clinic employee to approve before sending.
"""

from __future__ import annotations

from collections.abc import Mapping

from legal_core.contracts import DraftResponse, FactKey
from legal_core.risk_engine import RiskAssessment, RiskLevel

SAFE_OPERATIONAL_DRAFT_VERSION = "safe-operational-draft.v1"

_SAFE_TEXT = (
    "Здравствуйте. Мы получили и зарегистрировали ваше обращение. "
    "Чтобы корректно разобраться в ситуации, клиника проведёт внутреннюю проверку имеющейся "
    "документации и при необходимости предложит контрольный осмотр. "
    "До завершения проверки мы не будем делать выводы о причинах возникшей ситуации. "
    "После проверки сообщим вам дальнейший организационный порядок."
)
_RESCHEDULED_TEXT = (
    "Здравствуйте! Извините, что вам пришлось ждать приёма. "
    "Подтверждаем, что мы договорились о новой записи. "
    "Если нужно уточнить время, пожалуйста, напишите нам."
)
_FINANCE_TEXT = (
    "Здравствуйте! Мы получили ваше обращение по стоимости услуги. "
    "Проверим, какие услуги и цены были согласованы, и сверим выставленный счёт. "
    "После проверки сообщим вам результат."
)


def _blocked(reason_code: str) -> DraftResponse:
    return DraftResponse(
        status="BLOCKED",
        reasonCode=reason_code,
        policyVersion=SAFE_OPERATIONAL_DRAFT_VERSION,
    )


def _positive(value: object) -> bool:
    if value is True or value == "YES":
        return True
    if isinstance(value, dict):
        return value.get("boolean") is True or value.get("state") == "YES"
    return False


def build_safe_patient_draft(
    facts: Mapping[FactKey, object],
    risk: RiskAssessment,
) -> DraftResponse:
    """Return a bounded operational draft only when deterministic safety gates allow it."""

    if risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        return _blocked("HUMAN_LEGAL_REVIEW_REQUIRED")
    if risk.level is RiskLevel.UNAVAILABLE or not risk.external_draft_allowed:
        return _blocked("RISK_POLICY_BLOCKS_EXTERNAL_DRAFT")

    # Defense in depth: these signals should already make the deterministic risk policy high or
    # critical. If policy configuration ever drifts, patient-facing text still fails closed.
    if any(
        _positive(facts.get(key))
        for key in (
            FactKey.FORMAL_CLAIM,
            FactKey.HARM_CLAIMED,
            FactKey.HOSPITALIZATION,
            FactKey.LAWYER_CONTACT,
            FactKey.REGULATOR_OR_COURT,
        )
    ):
        return _blocked("PATIENT_DRAFT_SAFETY_SIGNAL")

    areas = facts.get(FactKey.SITUATION_AREAS)
    actions = facts.get(FactKey.CLINIC_ACTIONS)
    if (
        areas == ["SERVICE"]
        and isinstance(actions, list)
        and "REBOOKED_AFTER_WAIT" in actions
    ):
        draft = _RESCHEDULED_TEXT
    elif areas == ["FINANCE"]:
        draft = _FINANCE_TEXT
    else:
        draft = _SAFE_TEXT
    return DraftResponse(
        status="AVAILABLE",
        text=draft,
        policyVersion=SAFE_OPERATIONAL_DRAFT_VERSION,
    )
