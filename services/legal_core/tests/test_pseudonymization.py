from legal_core.pseudonymization import contains_obvious_direct_identifier, pseudonymize_text


def test_pseudonymizer_redacts_common_direct_identifiers() -> None:
    source = (
        "Пациент Иванов Иван Иванович, телефон +7 (999) 123-45-67, "
        "email patient@example.ru, паспорт 4510 123456."
    )

    result = pseudonymize_text(
        source,
        known_identifiers={"Иванов Иван Иванович": "[PATIENT_1]"},
    )

    assert "Иванов" not in result.text
    assert "999" not in result.text
    assert "example.ru" not in result.text
    assert "4510" not in result.text
    assert "[PATIENT_1]" in result.text
    assert "[PHONE]" in result.text
    assert "[EMAIL]" in result.text
    assert "[PASSPORT]" in result.text
    assert result.changed is True
    assert contains_obvious_direct_identifier(result.text) is False


def test_pseudonymizer_ignores_too_short_known_values() -> None:
    result = pseudonymize_text("Пациент ИИ сообщил о сколе.", known_identifiers={"ИИ": "[NAME]"})

    assert result.text == "Пациент ИИ сообщил о сколе."
    assert result.replacement_counts["known_identifier"] == 0


def test_direct_identifier_guard_detects_unredacted_contact() -> None:
    assert contains_obvious_direct_identifier("Позвонить 8 999 123 45 67") is True
    assert contains_obvious_direct_identifier("Пациент сообщил о сколе винира") is False
