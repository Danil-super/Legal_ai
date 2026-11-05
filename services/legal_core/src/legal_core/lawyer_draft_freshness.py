"""Compatibility fence for an unapproved internal lawyer draft."""

from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.analysis_api import AnalysisState
from legal_core.analysis_source_freshness import require_analysis_sources_current
from legal_core.case_api import ActorContext, ApiError
from legal_core.case_material_context import require_case_material_context_current


async def require_lawyer_draft_sources_current(
    session: AsyncSession, *, actor: ActorContext, state: AnalysisState,
) -> None:
    """Keep the draft contract while sharing the transaction-owned source fence."""
    try:
        await require_analysis_sources_current(session, actor=actor, state=state)
    except ApiError as error:
        if error.code != "ANALYSIS_CONTEXT_STALE":
            raise
        raise ApiError(status_code=409, code="LAWYER_DRAFT_CONTEXT_STALE",
                       message="Selected draft sources are no longer current") from error
    await require_case_material_context_current(
        session, clinic_id=actor.clinic_id, case_id=state.case.id,
        state=state.case_material_context,
    )
