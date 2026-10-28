"""Optional assistance after durable urgent routing; never replaces that receipt."""

import asyncio
import logging
import os
from uuid import UUID

from agent_orchestrator.hermes_client import HermesProtocolError, HermesUnavailable
from agent_orchestrator.lawyer_draft_generation import LawyerDraftGenerator
from agent_orchestrator.legal_core_client import (
    LegalCoreClient,
    LegalCoreError,
    LegalCoreProtocolError,
)
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.analysis_contracts import PreparationResult
from legal_core.case_preparation_contracts import HumanReviewResult
from legal_core.lawyer_draft_contracts import LawyerDraftContextRequest

logger = logging.getLogger(__name__)
# Fits inside Agent's 115 s overall wall, Core worker's 120/125 s HTTP/wall
# limits and the existing 180 s job lease. No extension/heartbeat is needed.
DRAFT_WALL_TIMEOUT_SECONDS = 110
RETURN_MARGIN_SECONDS = 1


async def maybe_generate_lawyer_draft(
    original: PreparationResult, *, core: LegalCoreClient, reasoning: LegalReasoningOrchestrator,
    telegram_user_id: int, idempotency_key: UUID, job_id: UUID | None,
    job_token: UUID | None, parent_deadline: float,
) -> None:
    if (os.getenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "0").strip().lower() not in {"true", "1"}
            or not isinstance(original.root, HumanReviewResult)
            or job_id is None or job_token is None or job_id != idempotency_key):
        return
    budget = min(DRAFT_WALL_TIMEOUT_SECONDS,
                 parent_deadline - asyncio.get_running_loop().time() - RETURN_MARGIN_SECONDS)
    if budget <= 0:
        return
    result = original.root
    binding = LawyerDraftContextRequest(
        expectedPreparationOperationId=result.source.operation_id,
        expectedFactSnapshotSha256=result.source.fact_snapshot_sha256,
        expectedRiskPolicyVersion=result.risk_policy_version,
    )
    try:
        async with asyncio.timeout(budget):
            lease = {"job_id": job_id, "job_token": job_token}
            retained = await core.lookup_lawyer_work_product(
                case_id=result.source.case_id, telegram_user_id=telegram_user_id,
                idempotency_key=idempotency_key, binding=binding, **lease,
            )
            if retained is not None:
                return
            context = await core.get_lawyer_draft_context(
                case_id=result.source.case_id, telegram_user_id=telegram_user_id,
                idempotency_key=idempotency_key, binding=binding, **lease,
            )
            if (context.escalation_id != result.escalation_id
                    or context.risk_level != result.risk_level):
                raise LegalCoreProtocolError("lawyer draft escalation binding mismatch")
            generated = await LawyerDraftGenerator(reasoning=reasoning).generate(context)
            if not generated.claims:
                return  # Honest abstention is not a fabricated completed plan.
            await core.submit_lawyer_work_product(
                context=context, reasoning=generated, telegram_user_id=telegram_user_id,
                idempotency_key=idempotency_key, **lease,
            )
        outcome = "STORED"
    except TimeoutError:
        outcome = "TIMEOUT"
    except (LegalCoreError, LegalCoreProtocolError, HermesUnavailable, HermesProtocolError,
            ValueError, TypeError, AttributeError):
        outcome = "UNAVAILABLE"
    # No exception messages, case text, tokens or raw upstream responses survive.
    logger.info("lawyer_draft_attempt job=%s outcome=%s", idempotency_key, outcome)
