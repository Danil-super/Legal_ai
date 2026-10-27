import pytest

from legal_core.pseudonymization import contains_obvious_direct_identifier, pseudonymize_text


@pytest.mark.parametrize("authority", [
    "Правительством Российской Федерации",
    "Президент Российской Федерации",
    "Конституция Российской Федерации",
])
def test_public_country_authority_is_not_a_patient_name(authority):
    assert contains_obvious_direct_identifier(authority) is False
    assert pseudonymize_text(authority).text == authority
    combined = f"{authority}. Иванов Иван Иванович, +7 999 123-45-67."
    assert contains_obvious_direct_identifier(combined) is True
    redacted = pseudonymize_text(combined)
    assert authority in redacted.text
    assert "Иванов" not in redacted.text and "999" not in redacted.text


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


def test_pseudonymizer_redacts_russian_initials_with_optional_spaces() -> None:
    source = "Пациент Иванов И. И. и врач П. П. Петров обсуждали повторное лечение."

    result = pseudonymize_text(source)

    assert "Иванов И. И." not in result.text
    assert "П. П. Петров" not in result.text
    assert result.text.count("[PERSON_NAME]") == 2
    assert result.replacement_counts["initials_name"] == 2
    assert contains_obvious_direct_identifier(result.text) is False


def test_pseudonymizer_redacts_full_name_when_patient_context_is_present() -> None:
    source = "Пациент Иван Иванов просит передать вопрос юристу."
    result = pseudonymize_text(source)

    assert result.changed is True
    assert result.text == "[PERSON_NAME] просит передать вопрос юристу."
    assert result.replacement_counts["identified_full_name"] == 1
    assert contains_obvious_direct_identifier(source) is True


def test_pseudonymizer_redacts_unlabelled_russian_full_name_fail_closed() -> None:
    source = "Иванов Иван Иванович сообщил о боли после лечения."

    result = pseudonymize_text(source)

    assert result.text == "[PERSON_NAME] сообщил о боли после лечения."
    assert result.replacement_counts["unlabelled_full_name"] == 1
    assert contains_obvious_direct_identifier(result.text) is False


def test_pseudonymizer_ignores_too_short_known_values() -> None:
    result = pseudonymize_text("Пациент ИИ сообщил о сколе.", known_identifiers={"ИИ": "[NAME]"})

    assert result.text == "Пациент ИИ сообщил о сколе."
    assert result.replacement_counts["known_identifier"] == 0


def test_direct_identifier_guard_detects_unredacted_contact() -> None:
    assert contains_obvious_direct_identifier("Позвонить 8 999 123 45 67") is True
    assert contains_obvious_direct_identifier("Пациент сообщил о сколе винира") is False


def test_direct_identifier_guard_detects_spaced_initials_name() -> None:
    assert contains_obvious_direct_identifier("Пациент Иванов И. И. сообщил о сколе") is True
    assert contains_obvious_direct_identifier("Врач П. П. Петров осмотрел пациента") is True


@pytest.mark.parametrize("punctuation", [".", ". ", ",", ";", ":", "!", "?", ")", "]", "…"])
def test_email_before_sentence_punctuation_is_detected_and_redacted(punctuation):
    source = "Синтетический адрес patient@example.invalid" + punctuation
    assert contains_obvious_direct_identifier(source)
    result = pseudonymize_text(source)
    assert result.text == "Синтетический адрес [EMAIL]" + punctuation
    assert result.replacement_counts["email"] == 1
    assert not contains_obvious_direct_identifier(result.text)


@pytest.mark.parametrize("email", ["first.last@example.invalid", "user@example.invalid.subdomain",
                                    "user+tag@sub.example.invalid", "почта@пример.рф"])
def test_email_domain_boundaries_redact_the_complete_address(email):
    assert pseudonymize_text(email + ".").text == "[EMAIL]."


@pytest.mark.parametrize("text", ["schema@version", "Пункт 1.2.3.", "https://example.invalid."])
def test_email_punctuation_guard_does_not_redact_non_email_metadata(text):
    assert pseudonymize_text(text).text == text


@pytest.mark.parametrize("email", [
    "synthetic@example.xn--p1ai",
    "SYNTHETIC@EXAMPLE.XN--P1AI",
    "synthetic@xn--e1afmkfd.xn--p1ai",
    "synthetic+tag@sub.xn--e1afmkfd.xn--80asehdb",
    "synthetic@example.xn--fiqs8s",
])
@pytest.mark.parametrize("punctuation", ["", ".", ". ", ";", ")"])
def test_punycode_email_is_fully_redacted_with_sentence_punctuation_preserved(email, punctuation):
    source = "Синтетический адрес " + email + punctuation
    assert contains_obvious_direct_identifier(source)
    redacted = pseudonymize_text(source)
    assert redacted.text == "Синтетический адрес [EMAIL]" + punctuation
    assert redacted.replacement_counts["email"] == 1
    assert not contains_obvious_direct_identifier(redacted.text)


@pytest.mark.parametrize("text", [
    "https://xn--e1afmkfd.xn--p1ai.", "xn--p1ai", "Адрес без почты example.xn--p1ai.",
])
def test_punycode_domain_without_an_email_is_preserved(text):
    assert not contains_obvious_direct_identifier(text)
    assert pseudonymize_text(text).text == text
