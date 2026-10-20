"""Independent case-card compatibility and honest malformed-data presentation."""

import pytest
from telegram_gateway.escalation_workspace import full_case_text


@pytest.mark.parametrize("key,label", [
    ("FORMAL_CLAIM", "Письменная претензия"),
    ("HOSPITALIZATION", "Госпитализация"),
    ("HARM_CLAIMED", "Сообщение \u043e вреде здоровью"),
    ("LAWYER_CONTACT", "Обращение представителя"),
])
@pytest.mark.parametrize("value,answer", [(True, "да"), (False, "нет"), ("UNKNOWN", "неизвестно")])
def test_legacy_boolean_contract_preserves_positive_negative_and_unknown(key, label, value, answer):
    text = full_case_text({"facts": {key: value}})
    assert f"{label}: {answer}" in text
    assert "формат сведений не распознан" not in text


def test_full_legacy_card_keeps_known_authority_and_document_facts_readable():
    text = full_case_text({"facts": {
        "REGULATOR_OR_COURT": "YES", "REGULATOR_THREAT": "UNKNOWN",
        "REPRESENTATIVE_AUTHORITY": "NO", "AUTHORITY_KIND": "COURT",
        "CLINIC_DOCUMENTS": {"CONTRACT": "AVAILABLE", "MEDICAL_RECORD": "MISSING"},
    }})
    lower = text.casefold()
    assert "суд" in lower and "да" in lower
    assert "неизвестно" in lower and "полномоч" in lower and "нет" in lower
    assert "договор" in lower and "медицинск" in lower
    assert "не отображена" not in text
    for code in ("REGULATOR_OR_COURT", "REPRESENTATIVE_AUTHORITY", "COURT", "AVAILABLE", "MISSING"):
        assert code not in text


@pytest.mark.parametrize("value", [[], True, {
    "schemaVersion": "unsupported-future-schema", "hospitalizationReported": "NO",
    "amount": {"amountKopecks": True, "currency": "USD"},
}])
def test_malformed_safety_is_not_silently_dropped_or_interpreted_as_current_facts(value):
    text = full_case_text({"facts": {"FACTUAL_SAFETY_SCREENING": value}})
    assert "не отображ" in text or "не распознан" in text
    assert "0 \u0440\u0443\u0431. 01 коп." not in text
    assert "госпитализации: нет" not in text.casefold()
    assert "unsupported-future-schema" not in text


def test_full_card_preserves_long_free_text_and_v2_unknown_precision():
    narrative = "Учебное описание <не является HTML> & содержит подробности. " * 80
    text = full_case_text({"facts": {
        "EVENT_SUMMARY": narrative, "AFFECTED_SERVICES": ["консультация", "коррекция"],
        "EVENT_DATE": {"date": None, "precision": "UNKNOWN"},
        "FACTUAL_SAFETY_SCREENING": {
            "schemaVersion": "factual-safety-intake.v1", "hospitalizationReported": "UNKNOWN",
            "moneyRequested": "YES", "amount": {"amountKopecks": 1234567, "currency": "RUB"},
        },
    }})
    assert narrative in text and "консультация; коррекция" in text
    assert "дата неизвестна" in text and "госпитализации: неизвестно" in text.casefold()
    assert "12345 \u0440\u0443\u0431. 67 коп." in text


@pytest.mark.parametrize("level", ["LOW", "HIGH"])
def test_full_card_includes_readable_existing_report_without_internal_payload(level):
    from test_report_presentation import presentation_report

    report = presentation_report(level=level, guided=True).model_dump(mode="json", by_alias=True)
    text = full_case_text({"facts": report["facts"], "report": report, "riskLevel": level})
    for content in ("Синтетическая проверка.", "Синтетический проверенный вывод.",
                    "Синтетический акт", "Таблица не извлечена", "статья 10"):
        assert content in text
    for internal in ("internal-claim-1", "EVENT_DATE", "schemaVersion", "dental-risk.v4",
                     "Legal Core", "article:"):
        assert internal not in text
    if level == "LOW":
        assert "Синтетический черновик <ответа>." in text
    else:
        assert "требуется юрист" in text.casefold()
