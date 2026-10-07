"""Synthetic, text-free package evidence matrix contract checks."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from legal_core.package_evidence_matrix import PackageEvidenceRequest


def _inventory() -> dict:
    groups = (["clinical"] * 7 + ["labour"] * 7 + ["courts"] * 10
              + ["privacy"] * 4 + ["licensing"] * 3 + ["healthcare"] * 22
              + ["general"] * 5)
    originals = []
    for index, group in enumerate(groups):
        kind = ("CLINICAL_REFERENCE" if group == "clinical" else
                "REFERENCE_FORM" if group == "healthcare" and index == 52 else
                "NORMATIVE")
        part_count = (4 if index == 53 else 2 if index == 54 else 1)
        originals.append({
            "material_id": str(uuid4()), "raw_sha256": f"{index + 1:064x}",
            "kind": kind, "group_key": group,
            "expected_part_keys": ([f"part-{n}" for n in range(1, part_count + 1)]
                                   if kind == "NORMATIVE" else []),
        })
    return {
        "schema_version": "package-evidence.v1", "package_key": "synthetic-package",
        "originals": originals,
        "legacy_version_ids": [str(uuid4()) for _ in range(6)],
    }


def test_inventory_requires_exact_package_shape_without_document_text() -> None:
    request = PackageEvidenceRequest.model_validate(_inventory())
    assert len(request.originals) == 58
    assert sum(len(item.expected_part_keys) for item in request.originals) == 54
    assert sum(item.kind != "NORMATIVE" for item in request.originals) == 8
    assert "normalized_text" not in request.model_dump_json()


@pytest.mark.parametrize("change", ["duplicate_id", "missing_reference", "wrong_parts",
                                     "unknown_field", "wrong_group"])
def test_inventory_fails_closed_on_shape_or_extra_content(change: str) -> None:
    payload = _inventory()
    if change == "duplicate_id":
        payload["originals"][1]["material_id"] = payload["originals"][0]["material_id"]
    elif change == "missing_reference":
        payload["originals"].pop(0)
    elif change == "wrong_parts":
        payload["originals"][-1]["expected_part_keys"] = ["part-1", "part-3"]
    elif change == "unknown_field":
        payload["originals"][0]["normalized_text"] = "sensitive synthetic text"
    else:
        payload["originals"][0]["group_key"] = "general"
    with pytest.raises(ValidationError):
        PackageEvidenceRequest.model_validate(payload)
