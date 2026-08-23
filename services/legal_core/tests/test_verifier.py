from datetime import date
from uuid import UUID

import pytest
from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment
from legal_core.verifier import ClaimKind, ProposedClaim, VerificationResult, verify_claims


def _evidence(*, effective_from: date = date(2026, 9, 1)) -> ApprovedLegalFragment:
    return ApprovedLegalFragment(
        fragment_id=UUID("00000000-0000-0000-0000-000000000001"),
        version_id=UUID("00000000-0000-0000-0000-000000000002"),
        document_id=UUID("00000000-0000-0000-0000-000000000003"),
        article=None,
        part=None,
        point="34",
        structural_path="point:34",
        fragment_text="Синтетический фрагмент нормы.",
        text_sha256="a" * 64,
        effective_from=effective_from,
        effective_to=None,
        source_url="https://publication.pravo.gov.ru/synthetic",
        raw_sha256="b" * 64,
        document_title="Синтетический акт",
        issuer="Synthetic authority",
        official_number="synthetic",
        version_date=date(2026, 5, 30),
        publication_date=date(2026, 6, 1),
    )


def test_verifier_accepts_a_claim_with_applicable_approved_evidence() -> None:
    result = verify_claims(
        [
            ProposedClaim(
                claim_id="claim-1",
                kind=ClaimKind.LEGAL,
                text="Внутренняя рекомендация на основе синтетического фрагмента.",
                evidence_fragment_ids=(UUID("00000000-0000-0000-0000-000000000001"),),
                required_fact_keys=(FactKey.FORMAL_CLAIM,),
            )
        ],
        evidence=[_evidence()],
        facts={FactKey.FORMAL_CLAIM: "YES"},
        as_of_date=date(2026, 9, 1),
    )

    assert result.analysis_allowed is True
    assert result.claims[0].result is VerificationResult.VERIFIED
    assert result.claims[0].verified_fragment_ids == (
        UUID("00000000-0000-0000-0000-000000000001"),
    )


def test_verifier_blocks_a_claim_without_returned_evidence() -> None:
    result = verify_claims(
        [
            ProposedClaim(
                claim_id="claim-1",
                kind=ClaimKind.LEGAL,
                text="Неподтверждённый синтетический вывод.",
                evidence_fragment_ids=(UUID("00000000-0000-0000-0000-000000000099"),),
            )
        ],
        evidence=[_evidence()],
        facts={},
        as_of_date=date(2026, 9, 1),
    )

    assert result.analysis_allowed is False
    assert result.claims[0].result is VerificationResult.UNSUPPORTED
    assert result.claims[0].reason_code == "EVIDENCE_NOT_RETURNED"


def test_verifier_blocks_evidence_that_is_not_effective_on_case_date() -> None:
    result = verify_claims(
        [
            ProposedClaim(
                claim_id="claim-1",
                kind=ClaimKind.ACTION,
                text="Действие на основе будущей редакции.",
                evidence_fragment_ids=(UUID("00000000-0000-0000-0000-000000000001"),),
            )
        ],
        evidence=[_evidence(effective_from=date(2026, 9, 1))],
        facts={},
        as_of_date=date(2026, 8, 31),
    )

    assert result.analysis_allowed is False
    assert result.claims[0].result is VerificationResult.NOT_APPLICABLE
    assert result.claims[0].reason_code == "EVIDENCE_NOT_EFFECTIVE_ON_CASE_DATE"


def test_verifier_does_not_convert_unknown_fact_to_a_legal_claim() -> None:
    result = verify_claims(
        [
            ProposedClaim(
                claim_id="claim-1",
                kind=ClaimKind.LEGAL,
                text="Вывод, требующий факта о претензии.",
                evidence_fragment_ids=(UUID("00000000-0000-0000-0000-000000000001"),),
                required_fact_keys=(FactKey.FORMAL_CLAIM,),
            )
        ],
        evidence=[_evidence()],
        facts={FactKey.FORMAL_CLAIM: "UNKNOWN"},
        as_of_date=date(2026, 9, 1),
    )

    assert result.analysis_allowed is False
    assert result.claims[0].result is VerificationResult.INSUFFICIENT_FACTS
    assert result.claims[0].reason_code == "FORMAL_CLAIM_UNKNOWN"


def test_verifier_rejects_duplicate_claim_identifiers() -> None:
    claim = ProposedClaim(
        claim_id="claim-1",
        kind=ClaimKind.LEGAL,
        text="Синтетический вывод.",
        evidence_fragment_ids=(UUID("00000000-0000-0000-0000-000000000001"),),
    )

    with pytest.raises(ValueError, match="claim identifiers must be unique"):
        verify_claims(
            [claim, claim],
            evidence=[_evidence()],
            facts={},
            as_of_date=date(2026, 9, 1),
        )
