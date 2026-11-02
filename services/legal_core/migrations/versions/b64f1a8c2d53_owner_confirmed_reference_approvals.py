"""Record explicit owner attestations without impersonating independent reviewers.

Revision ID: b64f1a8c2d53
Revises: a53e0f7b1c42
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b64f1a8c2d53"
down_revision: str | None = "a53e0f7b1c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "reference_evaluation_review_events"
_OLD_DECISIONS = "decision IN ('APPROVE_FOR_EVALUATION', 'CHANGES_REQUIRED', 'REJECT', 'RETIRE')"
_GUARD = """
CREATE OR REPLACE FUNCTION public.guard_reference_evaluation_review_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE creator uuid;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'reference evaluation review events are append-only';
    END IF;
    SELECT created_by_membership_id INTO creator
    FROM public.reference_evaluation_case_versions
    WHERE id = NEW.reference_version_id AND clinic_id = NEW.clinic_id
      AND reference_case_id = NEW.reference_case_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reference evaluation review is invalid';
    END IF;
    {approval_check}
    RETURN NEW;
END;
$$;
"""
_OWNER_CHECK = """
    IF NEW.decision = 'OWNER_CONFIRMED_APPROVAL' THEN
        IF NEW.note IS DISTINCT FROM 'LAWYER_PROVIDED_AND_CONFIRMED' OR NOT EXISTS (
            SELECT 1 FROM public.clinic_users m
            JOIN public.users u ON u.id=m.user_id
            JOIN public.reference_evaluation_access_grants g
              ON g.clinic_id=m.clinic_id AND g.membership_id=m.id
            JOIN public.subscription_entitlements e
              ON e.clinic_id=m.clinic_id AND e.user_id=u.id
            WHERE m.id=NEW.reviewer_membership_id AND m.clinic_id=NEW.clinic_id
              AND m.role='CLINIC_OWNER' AND m.status='ACTIVE' AND u.status='ACTIVE'
              AND g.permission='CONTRIBUTOR' AND e.status='ACTIVE'
              AND e.starts_at<=clock_timestamp()
              AND (e.ends_at IS NULL OR e.ends_at>clock_timestamp())
        ) THEN
            RAISE EXCEPTION 'reference owner attestation is invalid';
        END IF;
    ELSIF creator=NEW.reviewer_membership_id THEN
        RAISE EXCEPTION 'reference evaluation review is invalid';
    END IF;
"""


def _replace_decision_constraint(expression: str) -> None:
    constraints = sa.inspect(op.get_bind()).get_check_constraints(_TABLE)
    matches = [c for c in constraints if "decision" in c["sqltext"]]
    if len(matches) != 1 or matches[0]["name"] is None:
        raise RuntimeError("reference review decision constraint is ambiguous")
    name = matches[0]["name"]
    op.drop_constraint(name, _TABLE, type_="check")
    op.create_check_constraint(name, _TABLE, expression)


def upgrade() -> None:
    _replace_decision_constraint(_OLD_DECISIONS[:-1] + ", 'OWNER_CONFIRMED_APPROVAL')")
    op.execute(_GUARD.format(approval_check=_OWNER_CHECK))


def downgrade() -> None:
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM public.reference_evaluation_review_events "
            "WHERE decision='OWNER_CONFIRMED_APPROVAL'"
        )
    )
    if count:
        raise RuntimeError("cannot downgrade retained owner reference attestations")
    _replace_decision_constraint(_OLD_DECISIONS)
    op.execute(
        _GUARD.format(
            approval_check="""
    IF creator=NEW.reviewer_membership_id THEN
        RAISE EXCEPTION 'reference evaluation review is invalid';
    END IF;
    """
        )
    )
