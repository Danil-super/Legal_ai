from legal_core.clinic_documents import MAX_FRAGMENT_CHARS, prepare_clinic_document_text


def test_prepare_clinic_document_text_is_deterministic_and_bounded() -> None:
    source = "\ufeff Раздел 1\r\n\r\n" + ("условие " * 400) + "\r\n\r\nРаздел 2"

    first = prepare_clinic_document_text(source)
    second = prepare_clinic_document_text(source)

    assert first == second
    assert first.normalized_text.startswith("Раздел 1")
    assert first.content_sha256 == second.content_sha256
    assert len(first.fragments) >= 3
    assert [fragment.ordinal for fragment in first.fragments] == list(
        range(1, len(first.fragments) + 1)
    )
    assert all(len(fragment.fragment_text) <= MAX_FRAGMENT_CHARS for fragment in first.fragments)
    assert all(len(fragment.text_sha256) == 64 for fragment in first.fragments)


def test_prepare_clinic_document_text_rejects_blank_input() -> None:
    try:
        prepare_clinic_document_text("  \r\n\t  ")
    except ValueError as error:
        assert "must not be empty" in str(error)
    else:  # pragma: no cover
        raise AssertionError("blank clinic document text must be rejected")
