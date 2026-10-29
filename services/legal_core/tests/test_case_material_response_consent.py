"""New upload receipts confirm consent without rewriting storage-only wire bytes."""

from uuid import uuid4

import pytest

from legal_core.case_material_contracts import CaseMaterialResponse


def payload():
    return {"id": str(uuid4()), "displayName": "Материал 1", "mimeType": "text/plain",
            "sizeBytes": 42, "createdAt": "2026-10-29T10:00:00Z"}


@pytest.mark.parametrize("marker", [None, False, True])
def test_response_only_adds_explicit_positive_consent(marker):
    original = payload()
    response = CaseMaterialResponse.model_validate(original | {"analysisUseAllowed": marker})
    expected = original | ({"analysisUseAllowed": True} if marker is True else {})
    assert response.model_dump(mode="json", by_alias=True) == expected
    assert CaseMaterialResponse.model_validate(original).model_dump(
        mode="json", by_alias=True,
    ) == original


@pytest.mark.parametrize("marker", ["true", "false", 1, 0])
def test_response_never_coerces_a_non_boolean_consent_marker(marker):
    with pytest.raises(ValueError):
        CaseMaterialResponse.model_validate(payload() | {"analysisUseAllowed": marker})
