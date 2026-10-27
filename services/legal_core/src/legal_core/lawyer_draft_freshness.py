"""Transaction-owned provenance fence for an unapproved internal lawyer draft."""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.analysis import evidence_trace_sha256
from legal_core.analysis_api import AnalysisState
from legal_core.case_api import ActorContext, ApiError
from legal_core.clinic_document_retrieval import (
    _FRAGMENT_COLUMNS as _CLINIC_COLUMNS,
)
from legal_core.clinic_document_retrieval import (
    ApprovedClinicDocumentFragment,
    clinic_document_context_trace_sha256,
)
from legal_core.legal_retrieval import _FRAGMENT_COLUMNS as _LEGAL_COLUMNS
from legal_core.legal_retrieval import ApprovedLegalFragment
from legal_core.models import LegalDocument, LegalSource, LegalVersion, User


def _stale() -> ApiError:
    return ApiError(status_code=409, code="LAWYER_DRAFT_CONTEXT_STALE",
                    message="Selected draft sources are no longer current")


async def require_lawyer_draft_sources_current(
    session: AsyncSession, *, actor: ActorContext, state: AnalysisState,
) -> None:
    """Lock selected provenance, then check its current approved/date-applicable views.

    Call after Case/Job/Escalation locks and before the caller's final access/lease
    checks. This function neither commits nor retrieves new evidence. Locks last
    until the caller commits/rolls back; no model/network I/O belongs after it.
    """
    if (state.case.clinic_id != actor.clinic_id or not state.evidence
            or len(state.evidence) > 30 or len(state.clinic_document_context) > 20):
        raise _stale()
    legal_ids = [item.fragment_id for item in state.evidence]
    clinic_ids = [item.fragment_id for item in state.clinic_document_context]
    if len(set(legal_ids)) != len(legal_ids) or len(set(clinic_ids)) != len(clinic_ids):
        raise _stale()

    # Match the legal approval writer's version-first order. Approved content
    # and approval events are immutable; reviewer role/status and source/document
    # metadata still need row locks before reading the approval view again.
    versions = list((await session.scalars(select(LegalVersion).where(
        LegalVersion.id.in_({item.version_id for item in state.evidence}),
    ).order_by(LegalVersion.id).with_for_update()
      .execution_options(populate_existing=True))).all())
    if len(versions) != len({item.version_id for item in state.evidence}):
        raise _stale()
    for model, ids in (
        (LegalSource, {item.source_id for item in versions}),
        (LegalDocument, {item.document_id for item in versions}),
        (User, {item.approved_by for item in versions if item.approved_by is not None}),
    ):
        await session.execute(select(model.id).where(model.id.in_(ids)).order_by(model.id)
                              .with_for_update(read=True))

    if clinic_ids:
        # Approval-event INSERTs take FK KEY SHARE on this exact version. FOR
        # UPDATE conflicts with that lock; FOR SHARE would permit the race.
        version_ids = sorted({str(item.version_id) for item in state.clinic_document_context})
        locked = list((await session.scalars(text(
            "SELECT id FROM clinic_document_versions WHERE clinic_id=:clinic_id "
            "AND id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR UPDATE"
        ), {"clinic_id": actor.clinic_id, "ids": version_ids})).all())
        if len(locked) != len(version_ids):
            raise _stale()
        await session.execute(text(
            "SELECT id FROM clinic_documents WHERE clinic_id=:clinic_id "
            "AND id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHARE"
        ), {"clinic_id": actor.clinic_id,
            "ids": sorted({str(item.document_id) for item in state.clinic_document_context})})

    # New statements after all actual waits use a fresh READ COMMITTED snapshot.
    # Preserve the original selection/order; a newly retrieved replacement must
    # not silently authorize claims generated from withdrawn evidence.
    legal_rows = (await session.execute(text(
        f"SELECT {_LEGAL_COLUMNS} FROM production_legal_fragments "
        "WHERE fragment_id=ANY(CAST(:ids AS uuid[])) AND effective_from<=:as_of "
        "AND (effective_to IS NULL OR :as_of<effective_to)"
    ), {"ids": [str(item) for item in legal_ids], "as_of": state.as_of_date})).mappings()
    legal = {row["fragment_id"]: ApprovedLegalFragment(**dict(row)) for row in legal_rows}
    if set(legal) != set(legal_ids):
        raise _stale()
    selected_legal = tuple(legal[item] for item in legal_ids)
    if (selected_legal != state.evidence or evidence_trace_sha256(
            selected_legal, as_of_date=state.as_of_date) != state.evidence_trace_sha256):
        raise _stale()

    selected_clinic: tuple[ApprovedClinicDocumentFragment, ...] = ()
    if clinic_ids:
        clinic_rows = (await session.execute(text(
            f"SELECT {_CLINIC_COLUMNS} FROM approved_clinic_document_fragments "
            "WHERE clinic_id=:clinic_id AND fragment_id=ANY(CAST(:ids AS uuid[])) "
            "AND (valid_from IS NULL OR valid_from<=:as_of) "
            "AND (valid_to IS NULL OR :as_of<valid_to)"
        ), {"clinic_id": actor.clinic_id, "ids": [str(item) for item in clinic_ids],
            "as_of": state.as_of_date})).mappings()
        clinic = {row["fragment_id"]: ApprovedClinicDocumentFragment(**dict(row))
                  for row in clinic_rows}
        if set(clinic) != set(clinic_ids):
            raise _stale()
        selected_clinic = tuple(clinic[item] for item in clinic_ids)
    if (selected_clinic != state.clinic_document_context
            or clinic_document_context_trace_sha256(selected_clinic, as_of_date=state.as_of_date)
            != state.clinic_document_context_trace_sha256):
        raise _stale()
