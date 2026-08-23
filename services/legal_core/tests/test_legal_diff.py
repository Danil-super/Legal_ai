import pytest
from legal_core.corpus_loader import CorpusFragment
from legal_core.legal_diff import ChangeKind, diff_fragment_selections


def _fragment(ordinal: int, path: str, text: str) -> CorpusFragment:
    return CorpusFragment(
        ordinal=ordinal,
        article=None,
        part=None,
        point=str(ordinal),
        heading=None,
        structural_path=path,
        text=text,
    )


def test_structural_diff_is_deterministic_and_uses_fragment_hashes() -> None:
    result = diff_fragment_selections(
        previous=[
            _fragment(1, "rule/1", "Неизменный синтетический фрагмент."),
            _fragment(2, "rule/2", "Прежняя редакция синтетического фрагмента."),
        ],
        candidate=[
            _fragment(1, "rule/1", "Неизменный синтетический фрагмент."),
            _fragment(2, "rule/2", "Новая редакция синтетического фрагмента."),
            _fragment(3, "rule/3", "Добавленный синтетический фрагмент."),
        ],
    )

    assert [change.kind for change in result.changes] == [
        ChangeKind.CHANGED,
        ChangeKind.ADDED,
    ]
    assert [change.structural_path for change in result.changes] == ["rule/2", "rule/3"]
    assert result.changes[0].previous_text_sha256 is not None
    assert result.changes[0].candidate_text_sha256 is not None
    assert result.changes[1].previous_text_sha256 is None
    assert len(result.sha256) == 64


def test_structural_diff_rejects_ambiguous_paths() -> None:
    duplicate = [
        _fragment(1, "rule/1", "Первый синтетический фрагмент."),
        _fragment(2, "rule/1", "Второй синтетический фрагмент."),
    ]

    with pytest.raises(ValueError, match="structural paths must be unique"):
        diff_fragment_selections(previous=duplicate, candidate=[])
