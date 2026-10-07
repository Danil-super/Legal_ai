# ruff: noqa: RUF001
"""Plain-language labels for bounded risk reasons; no interpretation of case prose."""

_REASONS = {
    "HOSPITALIZATION_REPORTED": "Сообщено о госпитализации; требуется проверка юриста.",
    "AUTHORITY_OR_COURT_DOCUMENT_REPORTED": (
        "Сообщено о документе суда или органа; содержание пока не проверено."
    ),
    "HEALTH_CONSEQUENCE_SIGNALS_UNKNOWN": (
        "Уточните сведения о последствиях для здоровья и госпитализации."
    ),
    "INCOMING_COMMUNICATION_UNKNOWN": "Уточните, какое сообщение или документ поступил.",
    "HOSPITALIZATION_CONFIRMATION_CONFLICT": (
        "Ответы о госпитализации расходятся; требуется уточнение."
    ),
    "REGULATOR_OR_COURT_CONFIRMATION_CONFLICT": (
        "Ответы о документе суда или органа расходятся; требуется уточнение."
    ),
}


def risk_reason_label(code: str) -> str:
    return _REASONS.get(code, code[:80])
