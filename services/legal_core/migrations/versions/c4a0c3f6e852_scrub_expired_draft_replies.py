"""Erase expired draft payloads without erasing replay prevention metadata.

Revision ID: c4a0c3f6e852
Revises: c3f9b2e5d741
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "c4a0c3f6e852"
down_revision: str | None = "c3f9b2e5d741"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _function(clear_replies: bool) -> str:
    # Retain the latest parent/material ordering gate, not the original pre-material
    # purge implementation. Downgrade restores that exact predecessor definition.
    path = Path(__file__).with_name("e9f0a1b2c3d4_add_isolated_case_materials.py")
    spec = importlib.util.spec_from_file_location("material_gated_draft_purge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("predecessor draft retention function missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    definition = str(module._DRAFT_PURGE_SQL)
    if not clear_replies:
        return definition
    anchor = "    )\n    SELECT count(*) INTO deleted_count FROM deleted;"
    if definition.count("RETURNING 1") != 1 or definition.count(anchor) != 1:
        raise RuntimeError("unexpected predecessor draft retention definition")
    clear = """    ), cleared AS (
        UPDATE public.idempotency_records i SET response_json = NULL
          FROM deleted d
         WHERE i.resource_type = 'TELEGRAM_INTAKE_DRAFT'
           AND i.resource_id = d.id AND i.clinic_id = d.clinic_id
           AND i.actor_membership_id = d.actor_membership_id
        RETURNING i.id
    )
    SELECT count(*) INTO deleted_count FROM deleted;"""
    return definition.replace(
        "RETURNING 1", "RETURNING draft.id, draft.clinic_id, draft.actor_membership_id"
    ).replace(anchor, clear)


def upgrade() -> None:
    op.execute(_function(True))
    # Backfill only payloads whose matching draft row was already removed; do not
    # erase active/archived/submitted rows, receipts, hashes, scope or actor ownership.
    op.execute("""
      UPDATE public.idempotency_records i SET response_json = NULL
       WHERE i.resource_type = 'TELEGRAM_INTAKE_DRAFT' AND i.response_json IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM public.telegram_intake_drafts d
           WHERE d.id = i.resource_id AND d.clinic_id = i.clinic_id
             AND d.actor_membership_id = i.actor_membership_id)
    """)


def downgrade() -> None:
    # TTL-expired private text intentionally cannot be recovered by rollback.
    # Existing function ownership/execute ACL remains unchanged in both directions.
    op.execute(_function(False))
