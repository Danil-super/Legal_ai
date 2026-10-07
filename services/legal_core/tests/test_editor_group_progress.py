"""Synthetic-only completeness rules for the seven editor groups."""

from uuid import uuid4

from legal_core.editor_groups import group_progress


def _version(state: str, *, linked: bool = True) -> dict[str, object]:
    return {
        "version_id": uuid4(), "material_id": uuid4() if linked else None,
        "review_state": state, "kind": "LEGAL_COPY", "preparation_kind": "NORMATIVE",
        "link_state": "EXACT_ORIGINAL" if linked else "UNLINKED_VERSION",
        "reference_reviewed": False, "expected_parts": 2, "linked_parts": 2,
    }


def _reference(reviewed: bool) -> dict[str, object]:
    return {
        "version_id": None, "material_id": uuid4(), "review_state": "METADATA_REQUIRED",
        "kind": "LEGAL_COPY", "preparation_kind": "REFERENCE_FORM",
        "link_state": "ORIGINAL", "reference_reviewed": reviewed,
        "expected_parts": 0, "linked_parts": 0,
    }


def test_mixed_group_is_complete_only_after_all_norms_and_references_are_reviewed() -> None:
    first = _version("APPROVED")
    second = _version("REVIEW_REQUIRED")
    reference = _reference(False)
    progress = group_progress([first, second, reference])
    assert progress["normativeVersions"] == 2
    assert progress["approvedNormativeVersions"] == 1
    assert progress["referenceMaterials"] == 1
    assert progress["reviewedReferenceMaterials"] == 0
    assert progress["complete"] is False

    assert group_progress([first, second | {"review_state": "APPROVED"},
                           reference | {"reference_reviewed": True}])["complete"] is True


def test_one_bound_part_never_makes_two_part_bundle_complete() -> None:
    first = _version("APPROVED")
    missing_second = {
        "version_id": None, "material_id": uuid4(), "review_state": "METADATA_REQUIRED",
        "kind": "LEGAL_COPY", "preparation_kind": "NORMATIVE",
        "link_state": "ORIGINAL", "reference_reviewed": False,
        "expected_parts": 2, "linked_parts": 1,
    }
    progress = group_progress([first, missing_second])
    assert progress["missingParts"] == 1
    assert progress["complete"] is False


def test_unknown_part_inventory_and_empty_group_are_never_complete() -> None:
    original = {
        "version_id": None, "material_id": uuid4(), "review_state": "METADATA_REQUIRED",
        "kind": "LEGAL_COPY", "preparation_kind": "NORMATIVE",
        "link_state": "ORIGINAL", "reference_reviewed": False,
        "expected_parts": 0, "linked_parts": 0,
    }
    assert group_progress([original])["unpreparedOriginals"] == 1
    assert group_progress([original])["complete"] is False
    assert group_progress([])["complete"] is False


def test_unlinked_version_is_visible_but_not_mistaken_for_a_bound_original() -> None:
    original = {
        "version_id": None, "material_id": uuid4(), "review_state": "METADATA_REQUIRED",
        "kind": "LEGAL_COPY", "preparation_kind": "NORMATIVE",
        "link_state": "ORIGINAL", "reference_reviewed": False,
        "expected_parts": 0, "linked_parts": 0,
    }
    progress = group_progress([_version("APPROVED", linked=False), original])
    assert progress["unlinkedVersions"] == 1
    assert progress["unpreparedOriginals"] == 1
    assert progress["complete"] is False


def test_unlinked_approved_version_still_blocks_group_completion() -> None:
    progress = group_progress([_version("APPROVED", linked=False)])
    assert progress["approvedNormativeVersions"] == 1
    assert progress["unlinkedVersions"] == 1
    assert progress["complete"] is False
