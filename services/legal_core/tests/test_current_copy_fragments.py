"""No reconstructed formula, heading, or medical input in evidence compilation."""

import pytest

from legal_core.current_copy_fragments import compile_text_layer


def test_fragments_are_exact_substrings_with_document_structure() -> None:
    text = "Статья 1. Пример\nСохраните письменное обращение и дату получения.\n\n" * 2
    result = compile_text_layer(text)
    assert result.fragments
    assert all(item.text in text and item.article == "1" for item in result.fragments)
    assert [item.ordinal for item in result.fragments] == list(range(1, len(result.fragments) + 1))


def test_formula_dependent_article_is_not_usable_legal_evidence() -> None:
    text = (
        "Статья 1. Расчёт\nПоказатель определяется по формуле:\n,\nгде коэффициент равен пяти.\n"
        "Статья 2. Обращение\nСохраните письменное обращение и дату получения.\n"
    )
    result = compile_text_layer(text)
    assert result.excluded_blocks == 1
    assert {item.article for item in result.fragments} == {"2"}
    assert all("коэффициент" not in item.text for item in result.fragments)


def test_long_article_never_splits_into_invalid_or_fabricated_text() -> None:
    text = "Статья 1. Пример\n" + "Синтетическое предложение.\n" * 1500
    result = compile_text_layer(text)
    assert len(result.fragments) > 1
    assert all(20 <= len(item.text) <= 10_000 and item.text in text for item in result.fragments)


@pytest.mark.parametrize("text", [",\nгде\n", "Расчёт по формуле без извлечённого изображения."])
def test_no_usable_fragment_fails_closed(text: str) -> None:
    with pytest.raises(ValueError, match="no usable"):
        compile_text_layer(text)
