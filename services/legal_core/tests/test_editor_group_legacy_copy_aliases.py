"""PostgreSQL SELECT regression for UI-only aliases, never legal approval.

Transaction-local tables shadow public metadata solely on this connection. This
isolates fixed registry keys from any real corpus and does not test/bypass the
production binding writer: the contract here is visibility of existing edges.
"""

import asyncio
import hashlib
import os
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from legal_core.database import database_url
from legal_core.editor_groups import editor_group_items, group_progress
from legal_core.models import (
    LegalDocument,
    LegalMaterialPreparation,
    LegalPreparedPartVersion,
    LegalReviewMaterial,
    LegalVersion,
)

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL required",
)

_ALIASES = [
    ("ru-government-decree-659-2026", "garant-414322049-document"),
    ("ru-civil-code-part-ii-2026-06-09", "garant-10164072-part-2"),
    ("ru-consumer-protection-law-2026-04-01", "garant-10106035-document"),
    ("ru-federal-law-152-fz-2026-07-26", "garant-12148567-document"),
    ("ru-federal-law-323-fz-2026-08-04", "garant-12191967-document"),
    ("ru-minzdrav-order-1051n-2022", "garant-403111701-document"),
]
_CASES = [(old, new, "exact") for old, new in _ALIASES] + [
    (*_ALIASES[0], defect) for defect in (
        "unbound", "different_date", "unknown_old_date", "unknown_new_date",
        "unlisted_old", "unlisted_new", "unlisted_unnumbered", "wrong_alias_target",
        "stale_preparation", "stale_version", "expired", "linked_old",
    )
]


@pytest.mark.parametrize(("old_key", "new_key", "defect"), _CASES)
def test_editor_alias_hides_only_unlinked_old_copy_with_current_exact_binding(
    old_key: str, new_key: str, defect: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        raw = b"Synthetic metadata-only UI alias fixture, not a legal document."
        digest = hashlib.sha256(raw).hexdigest()
        try:
            async with engine.connect() as connection, connection.begin():
                # LIKE without indexes/FKs/triggers: fixtures model query metadata,
                # never mutate public corpus or manufacture approval events.
                for table in (
                    "legal_documents", "legal_versions", "legal_review_materials",
                    "legal_material_preparations", "legal_prepared_part_versions",
                    "legal_reference_review_events",
                ):
                    await connection.execute(text(
                        f"CREATE TEMP TABLE {table} (LIKE public.{table} "
                        "INCLUDING DEFAULTS) ON COMMIT DROP"
                    ))
                async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                    old_document = LegalDocument(
                        id=uuid4(), canonical_key=(
                            f"unlisted-{uuid4().hex}"
                            if defect in {"unlisted_old", "unlisted_unnumbered"} else old_key
                        ), document_type="DECREE", title="Synthetic legacy receipt",
                        issuer="Synthetic issuer", official_number=(
                            None if defect == "unlisted_unnumbered" else "2300-I"
                        ),
                        adoption_date=None if defect == "unknown_old_date" else date(2000, 1, 1),
                    )
                    new_document = LegalDocument(
                        id=uuid4(), canonical_key=(
                            f"unlisted-{uuid4().hex}" if defect == "unlisted_new" else
                            _ALIASES[1][1] if defect == "wrong_alias_target" else new_key
                        ), document_type="Постановление", title="Synthetic prepared receipt",
                        issuer=None, official_number=(
                            None if defect == "unlisted_unnumbered" else "2300-1"
                        ), adoption_date=(
                            None if defect == "unknown_new_date" else
                            date(2001, 1, 1) if defect == "different_date" else date(2000, 1, 1)
                        ),
                    )

                    def version(document: LegalDocument, number: int = 1) -> LegalVersion:
                        return LegalVersion(
                            id=uuid4(), document_id=document.id, source_id=uuid4(),
                            version_no=number, source_external_id="synthetic",
                            source_url="https://example.invalid/synthetic",
                            effective_from=date(2000, 1, 1),
                            approval_state="REVIEW_REQUIRED", artifact_kind="NORMALIZED_EXCERPT",
                            raw_sha256=digest, raw_mime_type="text/plain", raw_bytes=raw,
                            raw_size_bytes=len(raw), normalized_text=raw.decode(),
                            normalized_sha256=digest, fragments_sha256=digest,
                            normalization_scope="SELECTED_EXCERPT",
                            parser_version="synthetic-ui-v1",
                        )

                    old_version, new_version = version(old_document), version(new_document)
                    if defect == "expired":
                        new_version.effective_to = date(2001, 1, 1)
                    material = LegalReviewMaterial(
                        id=uuid4(), package_key=f"synthetic-{uuid4().hex}",
                        original_filename="synthetic.txt", title="Synthetic receipt",
                        kind="LEGAL_COPY", source_name="Synthetic fixture",
                        raw_mime_type="text/plain", raw_sha256=digest, raw_bytes=raw,
                        raw_size_bytes=len(raw), received_at=datetime.now(UTC),
                    )

                    def preparation(revision: int) -> LegalMaterialPreparation:
                        return LegalMaterialPreparation(
                            id=uuid4(), material_id=material.id, raw_sha256=digest,
                            revision=revision, preparation_sha256=digest,
                            title="Synthetic receipt", kind="NORMATIVE", group_key="general",
                            metadata_json={"parts": [{"part_key": "document"}]},
                        )

                    current_preparation = preparation(1)
                    rows = [old_document, new_document, old_version, new_version,
                            material, current_preparation]
                    if defect != "unbound":
                        rows.append(LegalPreparedPartVersion(
                            id=uuid4(), material_id=material.id,
                            preparation_id=current_preparation.id, raw_sha256=digest,
                            part_key="document", part_text_sha256=digest,
                            legal_version_id=new_version.id, created_by_user_id=uuid4(),
                        ))
                    if defect == "stale_preparation":
                        rows.append(preparation(2))
                    if defect == "stale_version":
                        rows.append(version(new_document, 2))
                    if defect == "linked_old":
                        rows.append(LegalPreparedPartVersion(
                            id=uuid4(), material_id=material.id,
                            preparation_id=current_preparation.id, raw_sha256=digest,
                            part_key="old-document", part_text_sha256=digest,
                            legal_version_id=old_version.id, created_by_user_id=uuid4(),
                        ))
                    session.add_all(rows)
                    await session.flush()
                    items = editor_group_items()
                    visible = (await session.execute(select(items))).mappings().all()
                    old_items = [item for item in visible if item["version_id"] == old_version.id]
                    if defect == "exact":
                        assert old_items == [], "current exact copy must hide legacy UI duplicate"
                        assert any(item["version_id"] == new_version.id
                                   and item["link_state"] == "EXACT_ORIGINAL" for item in visible)
                        assert group_progress(visible)["unlinkedVersions"] == 0
                    elif defect == "linked_old":
                        assert len(old_items) == 1
                        assert old_items[0]["link_state"] == "EXACT_ORIGINAL"
                        assert group_progress(visible)["normativeVersions"] == 2
                    else:
                        assert len(old_items) == 1
                        assert old_items[0]["link_state"] == "UNLINKED_VERSION"
                        assert group_progress(visible)["unlinkedVersions"] >= 1
                    # This is a UI projection only; neither stored state is approved.
                    assert old_version.approval_state == "REVIEW_REQUIRED"
                    assert new_version.approval_state == "REVIEW_REQUIRED"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
