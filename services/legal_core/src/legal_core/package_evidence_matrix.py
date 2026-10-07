"""Operator-only, read-only evidence gap matrix for the fixed 58-file package.

No legal text is returned, no document identity is inferred, and no approval occurs.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from legal_core.contracts import ContractModel
from legal_core.material_preparation import Digest


PackageKind = Literal["NORMATIVE", "CLINICAL_REFERENCE", "REFERENCE_FORM"]
PackageGroup = Literal[
    "clinical", "labour", "courts", "privacy", "licensing", "healthcare", "general"
]
_GROUP_COUNTS = {
    "clinical": 7, "labour": 7, "courts": 10, "privacy": 4,
    "licensing": 3, "healthcare": 22, "general": 5,
}


class ExpectedOriginal(ContractModel):
    material_id: UUID
    raw_sha256: Digest
    kind: PackageKind
    group_key: PackageGroup
    expected_part_keys: list[str] = Field(max_length=4)

    @model_validator(mode="after")
    def valid_part_shape(self) -> "ExpectedOriginal":
        keys = self.expected_part_keys
        if self.kind == "CLINICAL_REFERENCE" and self.group_key != "clinical":
            raise ValueError("clinical reference group mismatch")
        if self.kind == "REFERENCE_FORM" and self.group_key != "healthcare":
            raise ValueError("reference form group mismatch")
        if self.kind == "NORMATIVE" and self.group_key == "clinical":
            raise ValueError("normative material cannot be clinical")
        if self.kind == "NORMATIVE":
            if keys != [f"part-{index}" for index in range(1, len(keys) + 1)] or not keys:
                raise ValueError("normative part keys must be contiguous from part-1")
        elif keys:
            raise ValueError("references cannot have normative parts")
        return self


class PackageEvidenceRequest(ContractModel):
    schema_version: Literal["package-evidence.v1"]
    package_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    originals: list[ExpectedOriginal] = Field(min_length=58, max_length=58)
    legacy_version_ids: list[UUID] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def fixed_package_shape(self) -> "PackageEvidenceRequest":
        items = self.originals
        if len({item.material_id for item in items}) != 58 or (
            len({item.raw_sha256 for item in items}) != 58
            or len(set(self.legacy_version_ids)) != 6
        ):
            raise ValueError("duplicate material, original hash or legacy version")
        if Counter(item.group_key for item in items) != _GROUP_COUNTS:
            raise ValueError("seven-group original inventory differs")
        if sum(item.kind == "NORMATIVE" for item in items) != 50 or (
            sum(item.kind == "CLINICAL_REFERENCE" for item in items) != 7
            or sum(item.kind == "REFERENCE_FORM" for item in items) != 1
            or sum(len(item.expected_part_keys) for item in items) != 54
        ):
            raise ValueError("50/54/8 package inventory differs")
        general = sorted(len(item.expected_part_keys) for item in items
                         if item.group_key == "general")
        if general != [1, 1, 1, 2, 4] or any(
            len(item.expected_part_keys) != 1 for item in items
            if item.kind == "NORMATIVE" and item.group_key != "general"
        ):
            raise ValueError("code-part inventory differs")
        return self
