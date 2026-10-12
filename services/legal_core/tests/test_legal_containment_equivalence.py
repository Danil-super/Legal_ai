"""Exact UTF8 containment equivalence; only small fictional text, no approvals."""

import asyncio
import hashlib
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from legal_core.database import database_url
from legal_core.models import LegalFragment
from test_legal_current_copy_approval import _candidate

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


@pytest.mark.parametrize(("corpus", "needles"), [
    ("Начало, середина, конец", ["конец", "Начало", "середина"]),
    ("абаба", ["аба", "баб", "аба"]),
    ("ABCABC", ["ABC", "ABC", "CABC"]),
    ("🙂🦷 текст", ["🦷", "🙂", "текст"]),
    ("café e\u0301", ["é", "e\u0301", ""]),
    ("café", ["e\u0301"]),
    ("[a-z]+ $.*", ["$.*", "[a-z]+"]),
    ("Произвольный текст", ["текст", "отсутствует"]),
    ("Начало " + "x" * 20_000 + " конец", ["Начало", "конец"]),
    ("", [""]),
])
def test_ordered_byte_search_matches_original_text_membership(corpus, needles):
    async def scenario():
        engine = create_async_engine(database_url())
        try:
            async with AsyncSession(engine) as session, session.begin():
                _, version, source, _ = _candidate()
                document = _candidate()[0].document
                document.id, source.id, version.id = uuid4(), uuid4(), uuid4()
                document.canonical_key = f"synthetic-containment-{version.id}"
                source.source_key = f"synthetic-containment-{version.id}"
                version.document_id, version.source_id = document.id, source.id
                session.add_all([document, source, version])
                await session.flush()
                session.add_all([
                    LegalFragment(
                        version_id=version.id, ordinal=i, structural_path=f"synthetic:{i}",
                        fragment_text=needle,
                        text_sha256=hashlib.sha256(needle.encode()).hexdigest(),
                    ) for i, needle in enumerate(needles, 1)
                ])
                await session.flush()
                parameters = {"id": version.id, "corpus": corpus}
                original = await session.scalar(text(
                    "SELECT NOT EXISTS (SELECT 1 FROM legal_fragments f "
                    "WHERE f.version_id=:id AND position(f.fragment_text IN :corpus)=0)"
                ), parameters)
                optimized = await session.scalar(text(
                    "SELECT public.legal_fragments_contained(:id, :corpus)"
                ), parameters)
                assert original == optimized == all(needle in corpus for needle in needles)
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
