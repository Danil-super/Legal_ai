from uuid import UUID

import pytest
from legal_core.verifier import (
    ClaimKind,
    ProposedClaim,
    VerificationDecision,
    VerificationResult,
    VerifiedClaim,
)
from legal_core.verifier_persistence import build_verifier_run_payload


def _claim(identifier: str = "claim-1") -> ProposedClaim:
    return ProposedClaim(
        claim_id=identifier,
        kind=ClaimKind.LEGAL,
        text="Синтетическое утверждение для проверки хранения.",
        evidence_fragment_ids=(UUID("00000000-0000-0000-0000-000000000001"),),
    )


def test_blocked_verifier_payload_contains_hashes_not_claim_text() -> None:
    payload = build_verifier_run_payload(
        [_claim()],
        VerificationDecision(
            claims=(
                VerifiedClaim(
                    claim_id="claim-1",
                    result=VerificationResult.UNSUPPORTED,
                    reason_code="EVIDENCE_NOT_RETURNED",
                    verified_fragment_ids=(),
                ),
            )
        ),
    )

    assert payload.verifier_status == "BLOCKED"
    assert payload.block_reason_codes == ("EVIDENCE_NOT_RETURNED",)
    assert len(payload.claims[0].claim_sha256) == 64
    assert not hasattr(payload.claims[0], "text")


def test_verifier_payload_rejects_mismatched_claim_ids() -> None:
    decision = VerificationDecision(
        claims=(
            VerifiedClaim(
                claim_id="other-claim",
                result=VerificationResult.VERIFIED,
                reason_code=None,
                verified_fragment_ids=(UUID("00000000-0000-0000-0000-000000000001"),),
            ),
        )
    )

    with pytest.raises(ValueError, match="claim identifiers do not match"):
        build_verifier_run_payload([_claim()], decision)
