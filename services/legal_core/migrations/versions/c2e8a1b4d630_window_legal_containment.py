"""Avoid whole-corpus suffix copies for densely split legal texts.

Revision ID: c2e8a1b4d630
Revises: c1d7f9a3b520
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2e8a1b4d630"
down_revision: str | None = "c1d7f9a3b520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "located := position(needle IN substring(normalized_bytes FROM search_from));"
_NEW = """located := position(needle IN substring(normalized_bytes FROM search_from FOR
              least(octet_length(normalized_bytes)::bigint,
                previous_length::bigint + octet_length(needle)::bigint + 4096)::integer));
            IF located = 0 THEN
              located := position(needle IN substring(normalized_bytes FROM search_from));
            END IF;"""


def _replace_definition(replacements: list[tuple[str, str]]) -> None:
    definition = op.get_bind().scalar(sa.text(
        "SELECT pg_get_functiondef('public.legal_fragments_contained(uuid,text)'::regprocedure)"
    ))
    if not isinstance(definition, str):
        raise RuntimeError("legal containment function missing")
    for old, new in replacements:
        if definition.count(old) != 1:
            raise RuntimeError("unexpected predecessor containment definition")
        definition = definition.replace(old, new)
    op.execute(definition)


def upgrade() -> None:
    _replace_definition([
        (_OLD, _NEW),
        ("needle bytea;", "needle bytea;\n          previous_length integer := 0;"),
        ("search_from := search_from + located - 1;",
         "search_from := search_from + located - 1;\n"
         "              previous_length := octet_length(needle);"),
    ])


def downgrade() -> None:
    _replace_definition([
        (_NEW, _OLD),
        ("needle bytea;\n          previous_length integer := 0;", "needle bytea;"),
        ("search_from := search_from + located - 1;\n"
         "              previous_length := octet_length(needle);",
         "search_from := search_from + located - 1;"),
    ])
