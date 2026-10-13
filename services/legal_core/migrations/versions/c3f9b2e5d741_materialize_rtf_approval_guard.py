"""Materialize the identical RTF control check once per approval revalidation.

Revision ID: c3f9b2e5d741
Revises: c2e8a1b4d630
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f9b2e5d741"
down_revision: str | None = "c2e8a1b4d630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = """NOT EXISTS (SELECT 1 FROM unnest(ARRAY['object','objdata','objclass','objupdate']) c
         WHERE position(repeat(chr(92), 2) || c IN lower(encode(v.raw_bytes, 'escape'))) > 0)"""
_NEW = "public.legal_rtf_controls_absent(v.raw_bytes)"


def _replace_guard(old: str, new: str) -> None:
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef("
        "'public.legal_approval_event_is_current(legal_approval_events)'::regprocedure)"
    ))
    if not isinstance(definition, str) or definition.count(old) != 1:
        raise RuntimeError("unexpected predecessor RTF approval guard")
    op.execute(definition.replace(old, new))


def upgrade() -> None:
    # PL/pgSQL local defaults are evaluated on block entry, never across requests:
    # https://www.postgresql.org/docs/16/plpgsql-declarations.html
    op.execute("""
      CREATE FUNCTION public.legal_rtf_controls_absent(p_raw bytea)
      RETURNS boolean LANGUAGE plpgsql STABLE STRICT
      SET search_path = pg_catalog AS $$
      DECLARE
        escaped CONSTANT text := lower(encode(p_raw, 'escape'));
        control text;
      BEGIN
        FOREACH control IN ARRAY ARRAY['object','objdata','objclass','objupdate']
        LOOP
          IF position(repeat(chr(92), 2) || control IN escaped) > 0 THEN
            RETURN false;
          END IF;
        END LOOP;
        RETURN true;
      END;
      $$
    """)
    # No changes to role/source/event/hash/date/fragment checks or approval history.
    _replace_guard(_OLD, _NEW)


def downgrade() -> None:
    _replace_guard(_NEW, _OLD)
    op.execute("DROP FUNCTION public.legal_rtf_controls_absent(bytea)")
