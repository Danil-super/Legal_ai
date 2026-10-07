# ruff: noqa: RUF001
"""Plain-language labels for bounded risk reasons; no interpretation of case prose."""

from telegram_gateway.factual_safety_intake import QUESTIONS

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
    "FACTUAL_SAFETY_SCREENING_UNKNOWN": "Нужны независимые уточнения фактов перед анализом.",
    "FACTUAL_SAFETY_SCREENING_INVALID": "Уточнения фактов некорректны; требуется проверка.",
    "FACTUAL_SAFETY_SCREENING_CONFLICT": (
        "Ответы об обстоятельствах расходятся; требуется уточнение."
    ),
    "HEALTH_DETERIORATION_REPORTED": "Сообщено об ухудшении или осложнении; нужна проверка юриста.",
    "WRITTEN_REQUIREMENTS_REPORTED": "Получены письменные требования; нужна проверка юриста.",
}
_REASONS.update(
    {f"FACTUAL_SAFETY_{field.upper()}_UNKNOWN": question for field, question in QUESTIONS.items()}
)


def risk_reason_label(code: str) -> str:
    return _REASONS.get(code, code[:80])
