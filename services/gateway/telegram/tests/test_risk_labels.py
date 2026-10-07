# ruff: noqa: RUF001
from telegram_gateway.risk_labels import risk_reason_label


def test_guided_court_document_is_presented_as_a_report_not_a_legal_finding():
    assert risk_reason_label("AUTHORITY_OR_COURT_DOCUMENT_REPORTED") == (
        "Сообщено о документе суда или органа; содержание пока не проверено."
    )


def test_guided_unknown_health_asks_for_facts_in_plain_language():
    assert risk_reason_label("HEALTH_CONSEQUENCE_SIGNALS_UNKNOWN") == (
        "Уточните сведения о последствиях для здоровья и госпитализации."
    )


def test_unrecognized_codes_remain_bounded_without_interpreting_prose():
    assert risk_reason_label("FUTURE_REASON") == "FUTURE_REASON"
    assert len(risk_reason_label("X" * 100)) == 80
