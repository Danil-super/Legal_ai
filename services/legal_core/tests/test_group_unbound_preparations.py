"""Metadata-only reproduction of the staged fifty-original preparation state."""

import asyncio
from types import SimpleNamespace
from uuid import UUID

from legal_core.editor_groups import group_progress
from legal_core.group_approval import group_preview


def test_partial_preparation_receipts_do_not_create_approvable_versions():
    rows = [{
        "title": f"Synthetic normative original {index}",
        "kind": "LEGAL_COPY", "preparation_kind": "NORMATIVE",
        "version_id": None, "preparation_id": UUID(int=index + 1),
        "material_id": UUID(int=index + 101), "expected_parts": (
            4 if index == 0 else 2 if index == 1 else 1
        ), "linked_parts": 0, "link_state": "ORIGINAL", "reference_reviewed": False,
    } for index in range(50)]

    async def execute(statement):
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    preview = asyncio.run(group_preview(SimpleNamespace(execute=execute), "general"))
    assert not preview.ready and preview.already_approved == 0
    assert len(preview.blocked) == 50
    assert {item.reason_code for item in preview.blocked} == {"PARTS_UNBOUND"}
    assert group_progress(rows)["missingParts"] == 54
    assert not group_progress(rows)["complete"]
