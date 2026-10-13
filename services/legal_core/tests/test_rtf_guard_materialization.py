"""Keep the approval predicate equivalent when materializing RTF checks once."""

import os

import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("raw", [
    b"{\\rtf1 synthetic text}", b"{\\rtf1 \\u1040? synthetic}",
    *[b"{\\rtf1 \\" + word + b" synthetic}" for word in
      (b"object", b"objdata", b"objclass", b"objupdate", b"OBJECT", b"ObjData")],
    b"{\\rtf1 synthetic object without a control}", b"", bytes(range(256)),
    b"{\\rtf1 \\objclassSuffix}", b"{\\rtf1 \\\\objupdate}",
])
def test_materialized_rtf_guard_preserves_exact_predecessor_result(raw):
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            actual, expected = connection.execute(text("""
                SELECT public.legal_rtf_controls_absent(:raw),
                  NOT EXISTS (SELECT 1 FROM unnest(
                    ARRAY['object','objdata','objclass','objupdate']) c
                    WHERE position(repeat(chr(92),2) || c
                      IN lower(encode(CAST(:raw AS bytea),'escape'))) > 0)
            """), {"raw": raw}).one()
            assert actual is expected
    finally:
        engine.dispose()


def test_materialized_rtf_guard_is_used_by_current_approval_function():
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            definition = connection.execute(text("""
              SELECT pg_get_functiondef(
                'legal_approval_event_is_current(legal_approval_events)'::regprocedure)
            """)).scalar_one()
            assert "public.legal_rtf_controls_absent(v.raw_bytes)" in definition
            assert "legal_fragments_contained(v.id, v.normalized_text)" in definition
            assert "legal_regression_result_sha256" in definition
            assert "expectedEffectiveTo" in definition
    finally:
        engine.dispose()
