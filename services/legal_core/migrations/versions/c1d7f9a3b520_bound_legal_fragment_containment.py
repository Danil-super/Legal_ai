"""Recheck fragment containment without repeatedly decoding the complete UTF8 corpus.

Revision ID: c1d7f9a3b520
Revises: c0f6e8a2d419
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "c1d7f9a3b520"
down_revision: str | None = "c0f6e8a2d419"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _predecessor_guard() -> str:
    path = Path(__file__).with_name("c0f6e8a2d419_human_reviewed_current_copies.py")
    spec = importlib.util.spec_from_file_location("current_copy_guard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("predecessor approval guard is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module._approval_function())


def _optimized_guard() -> str:
    original = _predecessor_guard()
    containment = "OR position(f.fragment_text IN v.normalized_text) = 0"
    anchor = "AND v.fragments_sha256 = ("
    if original.count(containment) != 1 or original.count(anchor) != 1:
        raise RuntimeError("unexpected predecessor containment guard")
    # Keep every raw/source/editor/event/hash/date check, including per-fragment hashes.
    return original.replace(containment, "").replace(
        anchor,
        "AND public.legal_fragments_contained(v.id, v.normalized_text)\n               " + anchor,
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.legal_fragments_contained(p_version_id uuid, p_normalized_text text)
        RETURNS boolean LANGUAGE plpgsql STABLE STRICT AS $$
        DECLARE
          normalized_bytes bytea := convert_to(p_normalized_text, 'UTF8');
          needle bytea;
          fragment record;
          search_from integer := 1;
          located integer;
        BEGIN
          -- UTF8 is self-synchronizing: exact valid-text containment is equivalent
          -- to containment of its UTF8 bytes. No case folding or normalization occurs.
          -- Materialize once per call, not a cross-query cache. No approval is inferred.
          FOR fragment IN
            SELECT f.fragment_text FROM public.legal_fragments f
             WHERE f.version_id = p_version_id ORDER BY f.ordinal
          LOOP
            needle := convert_to(fragment.fragment_text, 'UTF8');
            located := position(needle IN substring(normalized_bytes FROM search_from));
            IF located > 0 THEN
              -- Keep the match start: overlapping or repeated fragments remain valid.
              search_from := search_from + located - 1;
            ELSIF position(needle IN normalized_bytes) = 0 THEN
              -- Ordering is only an optimization, never a new approval requirement.
              RETURN false;
            END IF;
          END LOOP;
          RETURN true;
        END;
        $$
        """
    )
    op.execute(_optimized_guard())


def downgrade() -> None:
    op.execute(_predecessor_guard())
    op.execute("DROP FUNCTION public.legal_fragments_contained(uuid, text)")
