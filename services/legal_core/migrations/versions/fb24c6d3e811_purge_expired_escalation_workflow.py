"""Include immutable lawyer workflow events in the controlled case-retention purge."""

import sqlalchemy as sa
from alembic import op

revision = "fb24c6d3e811"
down_revision = "fa93c5b2d710"
branch_labels = None
depends_on = None

_DELETE = "DELETE FROM public.case_escalations"
_WORKFLOW_DISABLE = (
    "EXECUTE 'ALTER TABLE public.case_escalation_workflow_events "
    "DISABLE TRIGGER escalation_workflow_immutable';\n            "
)
_WORKFLOW_ENABLE = (
    "EXECUTE 'ALTER TABLE public.case_escalation_workflow_events "
    "ENABLE TRIGGER escalation_workflow_immutable';\n            "
)
_WORKFLOW_DELETE = (
    "DELETE FROM public.case_escalation_workflow_events "
    "WHERE clinic_id = target.clinic_id AND escalation_id IN "
    "(SELECT id FROM public.case_escalations "
    "WHERE clinic_id = target.clinic_id AND case_id = target.id);\n            "
)


def _definition() -> str:
    return str(
        op.get_bind().scalar(
            sa.text(
                "SELECT pg_get_functiondef('public.purge_expired_case_content()'::regprocedure)"
            )
        )
    )


def upgrade() -> None:
    # Extend the installed function instead of replacing it with a stale original: independent
    # migrations may already have added job cleanup. Fail rather than silently omit retention.
    definition = _definition()
    # The existing source splits EXECUTE string literals over lines. Normalize just those anchors.
    disable = "EXECUTE 'ALTER TABLE public.case_escalations DISABLE '\n                    'TRIGGER"
    enable = "EXECUTE 'ALTER TABLE public.case_escalations ENABLE '\n                    'TRIGGER"
    enable_outer = "EXECUTE 'ALTER TABLE public.case_escalations ENABLE '\n                'TRIGGER"
    if (
        definition.count(disable) != 1
        or definition.count(enable) != 1
        or definition.count(enable_outer) != 1
        or definition.count(_DELETE) != 1
    ):
        raise RuntimeError("Unexpected retention function: cannot safely add workflow cleanup")
    definition = definition.replace(disable, _WORKFLOW_DISABLE + disable)
    definition = definition.replace(enable, _WORKFLOW_ENABLE + enable)
    definition = definition.replace(enable_outer, _WORKFLOW_ENABLE + enable_outer)
    definition = definition.replace(_DELETE, _WORKFLOW_DELETE + _DELETE)
    op.execute(sa.text(definition))


def downgrade() -> None:
    definition = _definition()
    for addition in (_WORKFLOW_DISABLE, _WORKFLOW_ENABLE, _WORKFLOW_DELETE):
        definition = definition.replace(addition, "")
    op.execute(sa.text(definition))
