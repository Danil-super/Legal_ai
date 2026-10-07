"""Make existing checksum CHECK functions safe under pg_restore's empty search path."""

from alembic import op

revision = "b9e5f3a7c012"
down_revision = "a8d4e2f6b901"
branch_labels = None
depends_on = None

# Also the reviewed, narrowly scoped repair for a historical archive's pre-data phase.
# No search-path override, permissions, tables, checksum format or existing data changes.
HASH_FUNCTIONS_SQL = r"""
CREATE OR REPLACE FUNCTION public.legal_canonical_jsonb(payload jsonb)
RETURNS text AS $$
DECLARE canonical text;
BEGIN
  CASE pg_catalog.jsonb_typeof(payload)
    WHEN 'object' THEN
      SELECT '{' || coalesce(pg_catalog.string_agg(
               pg_catalog.to_jsonb(item_key)::text || ':' ||
                 public.legal_canonical_jsonb(item_value),
               ',' ORDER BY item_key COLLATE pg_catalog."C"), '') || '}'
        INTO canonical
        FROM pg_catalog.jsonb_each(payload) AS items(item_key, item_value);
    WHEN 'array' THEN
      SELECT '[' || coalesce(pg_catalog.string_agg(
               public.legal_canonical_jsonb(item_value), ',' ORDER BY ordinal), '') || ']'
        INTO canonical
        FROM pg_catalog.jsonb_array_elements(payload) WITH ORDINALITY
             AS items(item_value, ordinal);
    ELSE
      canonical := payload::text;
  END CASE;
  RETURN canonical;
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;

CREATE OR REPLACE FUNCTION public.legal_regression_result_sha256(payload jsonb)
RETURNS text AS $$
  SELECT pg_catalog.encode(
    public.digest(pg_catalog.convert_to(public.legal_canonical_jsonb(payload), 'UTF8'), 'sha256'),
    'hex')
$$ LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE;
"""


def upgrade() -> None:
    op.execute(HASH_FUNCTIONS_SQL)


def downgrade() -> None:
    # The old schema/API uses these exact signatures and hashes. Keep compatible,
    # qualified definitions rather than deliberately reintroducing unsafe recovery.
    pass
