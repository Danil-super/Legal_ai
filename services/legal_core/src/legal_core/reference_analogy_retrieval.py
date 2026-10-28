"""Tenant-local, granted scenario analogies; no normative evidence or file reads."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.case_api import ActorContext
from legal_core.contracts import FactKey
from legal_core.models import (
    ClinicUser,
    ReferenceEvaluationAccessGrant,
    ReferenceEvaluationCase,
    ReferenceEvaluationCaseVersion,
    ReferenceEvaluationReviewEvent,
    SubscriptionEntitlement,
    User,
)
from legal_core.reference_analogy_contracts import (
    MAX_REFERENCE_CHARACTERS,
    MAX_REFERENCE_EXAMPLES,
    ReferenceAnalogyContext,
    ReferenceAnalogyExample,
    build_reference_analogy_context,
)

_WORDS = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_COMMON = frozenset({"пациент", "пациента", "пациенту", "пациентом", "клиника", "клиники",
                     "клинике", "клинику", "ситуация", "пример", "синтетический", "просит",
                     "после", "который", "которые", "были", "было", "этого", "также"})
MAX_REFERENCE_CANDIDATES = 200


def _terms(value: str) -> set[str]:
    return set(_WORDS.findall(value.casefold())) - _COMMON


async def select_reference_analogies(
    session: AsyncSession, *, actor: ActorContext, facts: Mapping[FactKey, object],
) -> ReferenceAnalogyContext:
    """Caller supplies its resolved tenant actor; this does not grant workspace access.

    Only complete, independently reviewed scenario text crosses this boundary.
    Selection is optional and local: unavailable/irrelevant/unsafe examples yield
    an empty context, never invented source authority or a weaker legal gate.
    Callers must reauthorize and fence this trace before accepting model output.
    """
    return await _select_reference_analogies_for_membership(
        session, clinic_id=actor.clinic_id, membership_id=actor.membership_id,
        user_id=actor.user_id, facts=facts,
    )


async def _select_reference_analogies_for_membership(
    session: AsyncSession, *, clinic_id: UUID, membership_id: UUID,
    facts: Mapping[FactKey, object], user_id: UUID | None = None,
) -> ReferenceAnalogyContext:
    """Internal source check may use a Case's actual producer; never a client-selected identity."""
    empty = build_reference_analogy_context([])
    now = datetime.now(UTC)
    permission_query = select(ReferenceEvaluationAccessGrant.id).join(
        ClinicUser, and_(ClinicUser.id == ReferenceEvaluationAccessGrant.membership_id,
                         ClinicUser.clinic_id == ReferenceEvaluationAccessGrant.clinic_id),
    ).join(User, User.id == ClinicUser.user_id).join(SubscriptionEntitlement, and_(
        SubscriptionEntitlement.user_id == User.id,
        SubscriptionEntitlement.clinic_id == ReferenceEvaluationAccessGrant.clinic_id,
    )).where(
        ReferenceEvaluationAccessGrant.clinic_id == clinic_id,
        ReferenceEvaluationAccessGrant.membership_id == membership_id,
        ReferenceEvaluationAccessGrant.permission.in_(("CONTRIBUTOR", "REVIEWER")),
        ClinicUser.status == "ACTIVE", User.status == "ACTIVE",
        SubscriptionEntitlement.status == "ACTIVE", SubscriptionEntitlement.starts_at <= now,
        or_(SubscriptionEntitlement.ends_at.is_(None),
            SubscriptionEntitlement.ends_at > now),
    )
    if user_id is not None:
        permission_query = permission_query.where(ClinicUser.user_id == user_id)
    permission = await session.scalar(permission_query.limit(1))
    if permission is None:
        return empty
    values: list[str] = []
    for key in (FactKey.EVENT_SUMMARY, FactKey.PROBLEM_SUMMARY, FactKey.AFFECTED_SERVICES):
        value = facts.get(key)
        if isinstance(value, str):
            values.append(value[:1500])
        elif isinstance(value, list):
            values.extend(item[:120] for item in value[:5] if isinstance(item, str))
    wanted = _terms(" ".join(values))
    if not wanted:
        return empty
    case, version, review = (ReferenceEvaluationCase, ReferenceEvaluationCaseVersion,
                             ReferenceEvaluationReviewEvent)
    rows = (await session.execute(select(
        case.id.label("referenceCaseId"), case.group_key.label("groupKey"),
        version.id.label("versionId"), version.version, version.scenario_text.label("scenarioText"),
        version.scenario_sha256.label("scenarioSha256"),
        version.as_of_date.label("evaluationAsOfDate"), review.id.label("approvalEventId"),
    ).join(version, and_(version.reference_case_id == case.id,
                         version.clinic_id == case.clinic_id,
                         version.version == case.current_version)).join(review, and_(
        review.reference_case_id == case.id, review.clinic_id == case.clinic_id,
        review.reference_version_id == version.id, review.decision == "APPROVE_FOR_EVALUATION",
        review.reviewer_membership_id != version.created_by_membership_id,
    )).where(
        permission_query.exists(),
        case.clinic_id == clinic_id, case.status == "APPROVED_FOR_EVALUATION",
        version.content_purged_at.is_(None), version.scenario_text.is_not(None),
        or_(version.content_expires_at.is_(None), version.content_expires_at > now),
        func.char_length(func.coalesce(version.scenario_text, "")) <= MAX_REFERENCE_CHARACTERS,
    ).order_by(case.id).limit(MAX_REFERENCE_CANDIDATES))).mappings().all()
    ranked: list[tuple[int, ReferenceAnalogyExample]] = []
    for row in rows:
        supplied = dict(row)
        scenario = supplied.pop("scenarioText")
        score = len(wanted & _terms(scenario))
        if not score:
            continue
        try:
            item = ReferenceAnalogyExample.model_validate({"source": supplied,
                                                           "scenarioText": scenario})
        except ValidationError:
            continue  # Never redact or truncate a reviewed source into a different example.
        ranked.append((score, item))
    ranked.sort(key=lambda item: (-item[0], str(item[1].source.reference_case_id)))
    selected: list[ReferenceAnalogyExample] = []
    used = 0
    for _, item in ranked:
        if used + len(item.scenario_text) <= MAX_REFERENCE_CHARACTERS:
            selected.append(item)
            used += len(item.scenario_text)
            if len(selected) == MAX_REFERENCE_EXAMPLES:
                break
    return build_reference_analogy_context(selected)
