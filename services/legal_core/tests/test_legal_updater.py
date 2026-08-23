from legal_core.corpus_loader import CorpusFragment
from legal_core.legal_updater import build_review_candidate


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


def test_updater_only_creates_a_checksum_bound_review_candidate() -> None:
    candidate = build_review_candidate(
        document_key="paid-medical-services",
        previous=[_fragment(1, "rule/1", "Прежний синтетический текст нормы.")],
        proposed=[_fragment(1, "rule/1", "Новый синтетический текст нормы.")],
        raw_sha256="a" * 64,
        normalized_sha256="b" * 64,
    )

    assert candidate.status == "REVIEW_REQUIRED"
    assert candidate.auto_promotion_allowed is False
    assert candidate.document_key == "paid-medical-services"
    assert len(candidate.structural_diff.changes) == 1
    assert len(candidate.candidate_sha256) == 64


def test_updater_rejects_invalid_artifact_checksums() -> None:
    try:
        build_review_candidate(
            document_key="paid-medical-services",
            previous=[],
            proposed=[_fragment(1, "rule/1", "Синтетический текст новой нормы.")],
            raw_sha256="not-a-checksum",
            normalized_sha256="b" * 64,
        )
    except ValueError as error:
        assert str(error) == "raw SHA-256 must be a lowercase 64-character digest"
    else:  # pragma: no cover - documents mandatory fail-closed behaviour.
        raise AssertionError("invalid raw checksum was accepted")
