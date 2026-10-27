"""Unapproved lawyer-review claims over a source-bound Core context; no writes or tools."""

from agent_orchestrator.hermes_client import HermesProtocolError, HermesUnavailable
from agent_orchestrator.projection import build_projection_from_context
from agent_orchestrator.reasoning import LegalReasoningOrchestrator, ReasoningResult
from legal_core.lawyer_draft_contracts import LawyerDraftContextResponse


class LawyerDraftGenerator:
    def __init__(self, *, reasoning: LegalReasoningOrchestrator) -> None:
        self._reasoning = reasoning

    async def generate(self, supplied: LawyerDraftContextResponse) -> ReasoningResult:
        """Revalidate before model I/O; Core still owns all legal/authorization decisions."""
        projection = None
        try:
            context = LawyerDraftContextResponse.model_validate(supplied.model_dump(
                by_alias=True, warnings="error",
            ))
            projection = build_projection_from_context(context.context)
        except (ValueError, TypeError, AttributeError):
            pass
        if projection is None:
            raise ValueError("lawyer draft requires a valid bound Core context")

        failure: HermesUnavailable | HermesProtocolError
        try:
            result = await self._reasoning.reason(projection, lawyer_review_draft=True)
        except HermesUnavailable as error:
            failure = HermesUnavailable(reason=error.reason)
        except (HermesProtocolError, ValueError, TypeError, AttributeError):
            failure = HermesProtocolError("lawyer draft reasoning does not match its contract")
        else:
            return result
        # Neither transport requests nor Pydantic's input-bearing causes survive this boundary.
        raise failure
