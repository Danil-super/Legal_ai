"""Publish checked LEGAL claims without rewording them or widening their citations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from legal_core.contracts import LegalConclusion

if TYPE_CHECKING:
    from legal_core.verifier import ProposedClaim, VerificationDecision


def select_verified_legal_conclusions(
    claims: Sequence[ProposedClaim],
    verification: VerificationDecision,
) -> list[LegalConclusion]:
    """Use the final server verdict, never a flag supplied by a reasoning model.

    The all-or-nothing gate is deliberately unchanged. The output cites only the subset
    actually verified by the semantic reviewer and accepted by the structural verifier.
    ACTION claims continue through the separate recommendations path.
    """

    if not verification.analysis_allowed:
        return []
    by_id = {item.claim_id: item for item in verification.claims}
    claim_ids = [claim.claim_id for claim in claims]
    if (
        len(by_id) != len(verification.claims)
        or len(claim_ids) != len(set(claim_ids))
        or set(by_id) != set(claim_ids)
    ):
        raise ValueError("legal conclusion claims and verification results do not match")
    conclusions: list[LegalConclusion] = []
    for claim in claims:
        if claim.kind != "LEGAL":
            continue
        checked = by_id[claim.claim_id]
        if checked.result != "VERIFIED":
            raise ValueError("legal conclusion is not server-verified")
        if not set(checked.verified_fragment_ids).issubset(claim.evidence_fragment_ids):
            raise ValueError("verified citations do not belong to the proposed claim")
        conclusions.append(
            LegalConclusion(
                claimId=claim.claim_id,
                text=claim.text,
                evidenceFragmentIds=list(checked.verified_fragment_ids),
                requiredFactKeys=list(claim.required_fact_keys),
            )
        )
    return conclusions
