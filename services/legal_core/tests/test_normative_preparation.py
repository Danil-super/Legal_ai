"""Synthetic-only checks for conservative offline RTF identity candidates."""

import hashlib

import pytest

from legal_core.normative_preparation import inspect_normative_rtf


def _rtf(title: str, url: str | None, body: str = "") -> bytes:
    if url is None:
        heading = title
    else:
        heading = (
            '{\\field{\\*\\fldinst {HYPERLINK "' + url + '"}}'
            '{\\fldrslt {\\cs24 ' + title + '}}}'
        )
    return (
        "{\\rtf1\\ansi\\ansicpg1251\\pard\\plain\\s1\\qc "
        + heading + "\\par\\pard " + body + "}"
    ).encode("cp1251")


def test_heading_link_wins_over_a_body_cross_reference() -> None:
    title = 'Федеральный закон от 21 ноября 2011 г. N 323-ФЗ "О синтетическом праве"'
    heading_url = "https://internet.garant.ru/document/redirect/12191967/0"
    body_url = "https://internet.garant.ru/document/redirect/99999999/0"
    text = title + "\nСтатья 1. Синтетическая норма.\n"
    candidate = inspect_normative_rtf(
        _rtf(title, heading_url, body_url), text,
    )

    assert candidate.source_url == heading_url
    assert candidate.raw_sha256 == hashlib.sha256(_rtf(title, heading_url, body_url)).hexdigest()
    assert candidate.parts[0].official_number == "323-ФЗ"
    assert candidate.parts[0].adoption_date_candidate.isoformat() == "2011-11-21"
    assert candidate.parts[0].text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert candidate.parts[0].publication_date is None
    assert candidate.parts[0].version_date is None
    assert candidate.parts[0].effective_from is None


def test_body_only_link_and_mismatched_heading_do_not_claim_a_source() -> None:
    title = 'Федеральный закон от 1 января 2001 г. N 63-ФЗ "Синтетический"'
    url = "https://internet.garant.ru/document/redirect/11111111/0"

    body_only = inspect_normative_rtf(_rtf(title, None, url), title + "\nBody")
    mismatched = inspect_normative_rtf(_rtf("Wrong title", url), title + "\nBody")

    assert body_only.source_url is None
    assert mismatched.source_url is None
    assert "SOURCE_HEADING_UNVERIFIED" in body_only.blockers
    assert "SOURCE_HEADING_UNVERIFIED" in mismatched.blockers


def test_a_different_centered_style_cannot_impersonate_the_title_paragraph() -> None:
    title = 'Федеральный закон от 1 января 2001 г. N 63-ФЗ "Синтетический"'
    url = "https://internet.garant.ru/document/redirect/11111111/0"
    fake_style = _rtf(title, url).replace(b"\\s1\\qc", b"\\s10\\qc")

    candidate = inspect_normative_rtf(fake_style, title)

    assert candidate.source_url is None
    assert "SOURCE_HEADING_UNVERIFIED" in candidate.blockers


def test_same_official_number_in_another_year_has_a_distinct_candidate_identity() -> None:
    url = "https://internet.garant.ru/document/redirect/11111111/0"
    first = 'Федеральный закон от 31 мая 2002 г. N 63-ФЗ "Синтетический A"'
    second = 'Федеральный закон от 6 апреля 2011 г. N 63-ФЗ "Синтетический B"'

    a = inspect_normative_rtf(_rtf(first, url), first).parts[0]
    b = inspect_normative_rtf(_rtf(second, url), second).parts[0]

    assert a.official_number == b.official_number == "63-ФЗ"
    assert a.identity_sha256 != b.identity_sha256


def test_code_type_is_a_candidate_even_when_code_starts_the_heading() -> None:
    title = (
        "Кодекс Российской Федерации об административных правонарушениях "
        "от 1 января 2001 г. N 1-ФЗ"
    )
    url = "https://internet.garant.ru/document/redirect/11111111/0"
    part = inspect_normative_rtf(_rtf(title, url), title).parts[0]

    assert part.document_type == "Кодекс"
    assert part.issuer is None
    assert "ISSUER_NOT_IN_HEADING" in part.blockers


def test_impossible_heading_date_is_left_unset() -> None:
    title = 'Федеральный закон от 31 февраля 2011 г. N 1-ФЗ "Синтетический"'
    url = "https://internet.garant.ru/document/redirect/11111111/0"

    part = inspect_normative_rtf(_rtf(title, url), title).parts[0]

    assert part.adoption_date_candidate is None
    assert "ACT_DATE_NOT_FOUND" in part.blockers


@pytest.mark.parametrize("number", ["7н", "1051н", "323-ФЗ", "2300-I", "659"])
def test_official_number_preserves_letter_suffix_in_heading(number: str) -> None:
    title = f'Приказ Синтетического ведомства от 1 января 2001 г. N {number} "О примере"'
    url = "https://internet.garant.ru/document/redirect/11111111/0"
    part = inspect_normative_rtf(_rtf(title, url), title).parts[0]
    assert part.official_number == number


def test_code_parts_are_scoped_without_treating_repeal_notices_as_boundaries() -> None:
    title = (
        "Гражданский кодекс Российской Федерации (ГК РФ) "
        "(части первая, вторая, третья и четвертая)"
    )
    url = "https://internet.garant.ru/document/redirect/10164072/0"
    text = (
        title + "\nЧасть первая\nПринят Государственной Думой 1 января 2000 года\n"
        "Статья 1. Часть вторая утратила силу в этой фразе.\n"
        "1 января 2000 г.\nN 1-ФЗ\n"
        "Часть вторая\nПринята Государственной Думой 2 января 2000 года\n"
        "Статья 2. Синтетический текст.\n2 января 2000 г.\nN 2-ФЗ\n"
        "Часть третья\nПринята Государственной Думой 3 января 2000 года\n"
        "Статья 3. Синтетический текст.\n3 января 2000 г.\nN 3-ФЗ\n"
        "Часть четвертая\nПринята Государственной Думой 4 января 2000 года\n"
        "Статья 4. Синтетический текст.\n4 января 2000 г.\nN 4-ФЗ\n"
    )
    candidate = inspect_normative_rtf(_rtf(title, url), text)

    assert [part.part_key for part in candidate.parts] == [
        "part-1", "part-2", "part-3", "part-4",
    ]
    assert [part.official_number for part in candidate.parts] == [
        "1-ФЗ", "2-ФЗ", "3-ФЗ", "4-ФЗ",
    ]
    assert [part.text_start for part in candidate.parts] == [
        0, text.index("Часть вторая\n"), text.index("Часть третья\n"),
        text.index("Часть четвертая\n"),
    ]
    assert candidate.parts[-1].text_end == len(text)
    assert all(part.text_sha256 == hashlib.sha256(
        text[part.text_start:part.text_end].encode()
    ).hexdigest() for part in candidate.parts)


def test_missing_or_spurious_code_boundary_never_hides_intended_parts() -> None:
    title = "Налоговый кодекс Российской Федерации (НК РФ)"
    url = "https://internet.garant.ru/document/redirect/10900200/0"
    text = (
        title + "\nЧасть первая\nПринята Государственной Думой 1 января 2000 года\n"
        "Часть вторая утратила силу — не заголовок.\n"
        "Часть вторая\nЭто цитата без строки о принятии.\n"
    )
    candidate = inspect_normative_rtf(_rtf(title, url), text)

    assert [part.part_key for part in candidate.parts] == ["part-1", "part-2"]
    assert all(part.text_sha256 is None for part in candidate.parts)
    assert "CODE_PART_BOUNDARIES_UNVERIFIED" in candidate.blockers


def test_unsafe_rtf_and_oversized_extraction_fail_closed() -> None:
    title = "Синтетический заголовок"
    with pytest.raises(ValueError, match="unsafe"):
        inspect_normative_rtf(b"{\\rtf1\\ansi\\object\\objdata bad}", title)
    with pytest.raises(ValueError, match="empty"):
        inspect_normative_rtf(_rtf(title, None), "  ")


@pytest.mark.parametrize("extracted", ["\ufeff", " \ufeff \n\ufeff\n"])
def test_bom_only_extraction_fails_with_a_validation_error(extracted: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        inspect_normative_rtf(_rtf("Synthetic title", None), extracted)
