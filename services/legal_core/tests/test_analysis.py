from datetime import date
from uuid import UUID

from legal_core.analysis import analyze_frozen_case, evidence_trace_sha256
from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment
from legal_core.risk_engine import RiskLevel, RiskPolicy
from legal_core.verifier import ClaimKind, ProposedClaim, SemanticReview, SemanticVerdict


FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000001")


def _evidence() -> ApprovedLegalFragment:
    return ApprovedLegalFragment(
        fragment_id=FRAGMENT_ID,
        version_id=UUID("00000000-0000-0000-0000-000000000002"),
        document_id=UUID("00000000-0000-0000-0000-000000000003"),
        article="10",
        part=None,
        point=None,
        structural_path="article:10",
        fragment_text="Синтетический фрагмент нормы.",
        text_sha256="a" * 64,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        source_url="https://publication.pravo.gov.ru/synthetic",
        raw_sha256="b" * 64,
        document_title="Синтетический акт",
        issuer="Synthetic authority",
        official_number="synthetic",
        version_date=date(2026, 1, 1),
        publication_date=date(2026, 1, 1),
    )


def _facts(*, formal_claim: str = "NO") -> dict[FactKey, object]:
    return {
        FactKey.HARM_CLAIMED: "NO",
        FactKey.LAWYER_CONTACT: "NO",
        FactKey.FORMAL_CLAIM: formal_claim,
        FactKey.REGULATOR_OR_COURT: "NO",
        FactKey.REGULATOR_THREAT: "NO",
        FactKey.CLINIC_DOCUMENTS: {"CONTRACT": "AVAILABLE"},
    }


def _claim() -> ProposedClaim:
    return ProposedClaim(
        claim_id="claim-1",
        kind=ClaimKind.LEGAL,
        text="Синтетический внутренний правовой вывод.",
        evidence_fragment_ids=(FRAGMENT_ID,),
    )


def _review(verdict: SemanticVerdict = SemanticVerdict.SUPPORTED) -> SemanticReview:
    return SemanticReview(
        claim_id="claim-1",
        verdict=verdict,
        reviewed_fragment_ids=(FRAGMENT_ID,),
    )


def test_analysis_allows_verified_low_risk_case() -> None:
    outcome = analyze_frozen_case(
        facts=_facts(),
        as_of_date=date(2026, 8, 31),
        evidence=[_evidence()],
        claims=[_claim()],
        semantic_reviews=[_review()],
        risk_policy=RiskPolicy(
            version="risk-policy.v1", high_demand_threshold_kopecks=10_000_000
        ),
    )

    assert outcome.analysis_allowed is True
    assert outcome.risk.level is RiskLevel.LOW
    assert len(outcome.evidence_trace_sha256) == 64


def test_analysis_blocks_risk_when_semantic_verification_fails() -> None:
    outcome = analyze_frozen_case(
        facts=_facts(formal_claim="YES"),
        as_of_date=date(2026, 8, 31),
        evidence=[_evidence()],
        claims=[_claim()],
        semantic_reviews=[_review(SemanticVerdict.UNSUPPORTED)],
        risk_policy=RiskPolicy(
            version="risk-policy.v1", high_demand_threshold_kopecks=10_000_000
        ),
    )

    assert outcome.analysis_allowed is False
    assert outcome.risk.level is RiskLevel.UNAVAILABLE
    assert outcome.risk.reason_codes == ("EVIDENCE_NOT_VERIFIED",)


def test_analysis_raises_high_risk_only_after_evidence_gate_passes() -> None:
    outcome = analyze_frozen_case(
        facts=_facts(formal_claim="YES"),
        as_of_date=date(2026, 8, 31),
        evidence=[_evidence()],
        claims=[_claim()],
        semantic_reviews=[_review()],
        risk_policy=RiskPolicy(
            version="risk-policy.v1", high_demand_threshold_kopecks=10_000_000
        ),
    )

    assert outcome.analysis_allowed is True
    assert outcome.risk.level is RiskLevel.HIGH
    assert "FORMAL_CLAIM_RECEIVED" in outcome.risk.reason_codes


def test_evidence_trace_is_order_independent() -> None:
    first = _evidence()
    second = ApprovedLegalFragment(
        fragment_id=UUID("00000000-0000-0000-0000-000000000010"),
        version_id=UUID("00000000-0000-0000-0000-000000000011"),
        document_id=UUID("00000000-0000-0000-0000-000000000012"),
        article="11",
        part=None,
        point=None,
        structural_path="article:11",
        fragment_text="Другой синтетический фрагмент.",
        text_sha256="c" * 64,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        source_url="https://publication.pravo.gov.ru/synthetic-2",
        raw_sha256="d" * 64,
        document_title="Другой синтетический акт",
        issuer="Synthetic authority",
        official_number="synthetic-2",
        version_date=date(2026, 1, 1),
        publication_date=date(2026, 1, 1),
    )

    assert evidence_trace_sha256(
        [first, second], as_of_date=date(2026, 8, 31)
    ) == evidence_trace_sha256([second, first], as_of_date=date(2026, 8, 31))
