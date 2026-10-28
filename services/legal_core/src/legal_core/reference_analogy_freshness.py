"""Boolean provenance fence for the actual case producer, without exposing examples."""

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.case_api import ApiError
from legal_core.contracts import FactKey
from legal_core.models import (
    Case,
    ClinicUser,
    ReferenceEvaluationAccessGrant,
    ReferenceEvaluationCase,
    ReferenceEvaluationCaseVersion,
    SubscriptionEntitlement,
    User,
)
from legal_core.reference_analogy_retrieval import _select_reference_analogies_for_membership


def _stale() -> ApiError:
    return ApiError(status_code=409, code="REFERENCE_ANALOGY_CONTEXT_STALE",
                    message="The selected reviewed example sources are no longer current")


async def require_reference_analogy_trace_current(
    session: AsyncSession, *, case: Case, facts: Mapping[FactKey, object],
    expected_trace: str | None,
) -> None:
    """Caller already owns its case lock and authorization; no model/network calls follow.

    Recheck only the source producer stored on Case, never impersonate a viewer
    or expose private historical text through the work-product GET. Hold selected
    provenance and producer grant/access rows through the caller's commit/rollback.
    The producer feature flag does not revoke a valid historical plan's read path.
    """
    if expected_trace is None:
        return
    selected = await _select_reference_analogies_for_membership(
        session, clinic_id=case.clinic_id, membership_id=case.created_by_membership_id, facts=facts,
    )
    if not selected.examples or selected.trace_sha256 != expected_trace:
        raise _stale()
    # Match the review/retirement API's Case -> Version order.
    for model, ids in (
        (ReferenceEvaluationCase, {item.source.reference_case_id for item in selected.examples}),
        (ReferenceEvaluationCaseVersion, {item.source.version_id for item in selected.examples}),
    ):
        locked = (await session.scalars(select(model.id).where(
            model.clinic_id == case.clinic_id, model.id.in_(ids),
        ).order_by(model.id).with_for_update(read=True))).all()
        if len(locked) != len(ids):
            raise _stale()
    producer = (await session.execute(select(ClinicUser.id, ClinicUser.user_id).where(
        ClinicUser.clinic_id == case.clinic_id, ClinicUser.id == case.created_by_membership_id,
    ).with_for_update(read=True))).one_or_none()
    if producer is None:
        raise _stale()
    await session.execute(select(User.id).where(User.id == producer.user_id)
                          .with_for_update(read=True))
    await session.execute(select(SubscriptionEntitlement.id).where(
        SubscriptionEntitlement.clinic_id == case.clinic_id,
        SubscriptionEntitlement.user_id == producer.user_id,
    ).order_by(SubscriptionEntitlement.id).with_for_update(read=True))
    await session.execute(select(ReferenceEvaluationAccessGrant.id).where(
        ReferenceEvaluationAccessGrant.clinic_id == case.clinic_id,
        ReferenceEvaluationAccessGrant.membership_id == case.created_by_membership_id,
    ).order_by(ReferenceEvaluationAccessGrant.id).with_for_update(read=True))
    # A fresh READ COMMITTED statement after every actual wait repeats all access,
    # approval/version/live/hash/privacy predicates and the exact selected order.
    current = await _select_reference_analogies_for_membership(
        session, clinic_id=case.clinic_id, membership_id=case.created_by_membership_id, facts=facts,
    )
    if current != selected:
        raise _stale()
