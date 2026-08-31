"""Two-pass legal reasoning over a bounded Legal Core projection.

The first Hermes profile proposes structured internal claims.  A second profile receives the same
approved evidence and reviews each claim independently.  Legal Core still performs the final
server-side structural/date/risk gates; this module never declares a claim legally verified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from agent_orchestrator.contracts import (
    CaseProjection,
    ClaimProposalBatch,
    SemanticReviewBatch,
)
from agent_orchestrator.hermes_client import HermesClient, HermesProtocolError
from legal_core.contracts import FactKey
from legal_core.pseudonymization import contains_obvious_direct_identifier
from legal_core.verifier import (
    ClaimKind,
    ProposedClaim,
    SemanticReview,
    SemanticVerdict,
)


_RESEARCH_SYSTEM = """\
Ты — внутренний research-агент Dental Legal AI. Работай ТОЛЬКО с JSON-входом ниже.
Право можно выводить только из evidence, переданного Legal Core. Запрещено использовать память
модели как источник права, добавлять статьи/документы, которых нет во входе, или считать UNKNOWN
факт истинным. Не признавай вину клиники и не обещай выплату.

Верни ТОЛЬКО JSON-объект следующей формы:
{
  "claims": [
    {
      "claimId": "c1",
      "kind": "LEGAL" | "ACTION",
      "text": "краткий внутренний вывод",
      "evidenceFragmentIds": ["uuid"],
      "requiredFactKeys": ["FORMAL_CLAIM"]
    }
  ],
  "internalRecommendations": ["..."],
  "patientDraft": "..." | null
}

Каждый claim обязан ссылаться только на fragmentId из evidence. Если доказательств недостаточно,
не придумывай claim. patientDraft — только черновик спокойного ответа без признания ответственности.
"""

_REVIEW_SYSTEM = """\
Ты — независимый verifier Dental Legal AI. Тебе переданы approved evidence и claims другого
агента. Для КАЖДОГО claim реши, действительно ли его смысл поддерживается указанными фрагментами.
Не оценивай полезность формулировки и не добавляй новые нормы.

Верни ТОЛЬКО JSON:
{
  "reviews": [
    {
      "claimId": "c1",
      "verdict": "SUPPORTED" | "UNSUPPORTED" | "CONTRADICTED",
      "reviewedFragmentIds": ["uuid"]
    }
  ]
}

SUPPORTED разрешён только когда утверждение не шире и не категоричнее evidence. Если evidence
не доказывает утверждение — UNSUPPORTED. Если утверждение противоречит evidence — CONTRADICTED.
"""


@dataclass(frozen=True, slots=True)
class ReasoningResult:
    claims: tuple[ProposedClaim, ...]
    semantic_reviews: tuple[SemanticReview, ...]
    internal_recommendations: tuple[str, ...]
    patient_draft: str | None


class LegalReasoningOrchestrator:
    def __init__(self, *, researcher: HermesClient, reviewer: HermesClient) -> None:
        researcher_identity = (researcher.endpoint.base_url, researcher.endpoint.model)
        reviewer_identity = (reviewer.endpoint.base_url, reviewer.endpoint.model)
        if researcher_identity == reviewer_identity:
            raise ValueError(
                "researcher and semantic reviewer must use distinct Hermes profile identities"
            )
        self._researcher = researcher
        self._reviewer = reviewer

    async def reason(self, projection: CaseProjection) -> ReasoningResult:
        serialized_projection = projection.model_dump(mode="json", by_alias=True)
        research_input = json.dumps(serialized_projection, ensure_ascii=False, sort_keys=True)
        if contains_obvious_direct_identifier(research_input):
            raise ValueError("bounded case projection still contains an obvious direct identifier")

        research_raw = await self._researcher.complete_json(
            system=_RESEARCH_SYSTEM,
            user=research_input,
        )
        try:
            proposal = ClaimProposalBatch.model_validate(research_raw)
        except ValidationError as exc:
            raise HermesProtocolError("researcher JSON does not match the claim contract") from exc

        review_input = {
            "caseId": str(projection.case_id),
            "asOfDate": projection.as_of_date.isoformat(),
            "facts": projection.facts,
            "evidence": [item.model_dump(mode="json", by_alias=True) for item in projection.evidence],
            "claims": [item.model_dump(mode="json", by_alias=True) for item in proposal.claims],
        }
        review_raw = await self._reviewer.complete_json(
            system=_REVIEW_SYSTEM,
            user=json.dumps(review_input, ensure_ascii=False, sort_keys=True),
        )
        try:
            review = SemanticReviewBatch.model_validate(review_raw)
        except ValidationError as exc:
            raise HermesProtocolError("reviewer JSON does not match the semantic-review contract") from exc

        proposal_ids = {claim.claim_id for claim in proposal.claims}
        review_ids = {item.claim_id for item in review.reviews}
        if review_ids != proposal_ids:
            raise HermesProtocolError("semantic reviewer must return exactly one review per claim")

        claims = tuple(
            ProposedClaim(
                claim_id=item.claim_id,
                kind=ClaimKind(item.kind),
                text=item.text,
                evidence_fragment_ids=tuple(item.evidence_fragment_ids),
                required_fact_keys=tuple(FactKey(key) for key in item.required_fact_keys),
            )
            for item in proposal.claims
        )
        semantic_reviews = tuple(
            SemanticReview(
                claim_id=item.claim_id,
                verdict=SemanticVerdict(item.verdict),
                reviewed_fragment_ids=tuple(item.reviewed_fragment_ids),
            )
            for item in review.reviews
        )
        return ReasoningResult(
            claims=claims,
            semantic_reviews=semantic_reviews,
            internal_recommendations=tuple(proposal.internal_recommendations),
            patient_draft=proposal.patient_draft,
        )
