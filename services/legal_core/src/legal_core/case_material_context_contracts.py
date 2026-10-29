"""Private, source-bound excerpts from explicitly consented case attachments."""

import hashlib
import json
from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from legal_core.case_material_contracts import CaseMaterialMimeType
from legal_core.contracts import ContractModel

Limitation = Annotated[str, Field(strict=True, min_length=1, max_length=160)]


class CaseMaterialContextFragment(ContractModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True,
                              revalidate_instances="always", hide_input_in_errors=True)
    material_id: UUID = Field(alias="materialId")
    fragment_id: UUID = Field(alias="fragmentId")
    display_name: str = Field(alias="displayName", pattern=r"^Материал (?:[1-9]|10)$")
    mime_type: CaseMaterialMimeType = Field(alias="mimeType")
    raw_sha256: str = Field(alias="rawSha256", pattern=r"^[0-9a-f]{64}$")
    text_sha256: str = Field(alias="textSha256", pattern=r"^[0-9a-f]{64}$")
    structural_path: str = Field(alias="structuralPath", min_length=1, max_length=500)
    text: str = Field(strict=True, min_length=1, max_length=1200)
    extraction_limitations: list[Limitation] = Field(
        default_factory=list, alias="extractionLimitations", max_length=8,
    )

    @model_validator(mode="after")
    def exact_text_hash(self) -> "CaseMaterialContextFragment":
        if (not self.text.strip()
                or hashlib.sha256(self.text.encode()).hexdigest() != self.text_sha256):
            raise ValueError("case material excerpt hash is invalid")
        return self


def case_material_context_trace_sha256(
    fragments: Sequence[CaseMaterialContextFragment],
) -> str | None:
    """All eligible files are represented; bind their identities, hashes and selected text."""
    if not fragments:
        return None
    if len(fragments) > 12:
        raise ValueError("too many case material excerpts")
    checked = [CaseMaterialContextFragment.model_validate(
        item.model_dump(mode="json", by_alias=True),
    ) for item in fragments]
    if (len({item.fragment_id for item in checked}) != len(checked)
            or len({item.material_id for item in checked}) > 10):
        raise ValueError("case material context identities are invalid")
    payload = {"kind": "CASE_MATERIAL_CONTEXT", "fragments": [
        item.model_dump(mode="json", by_alias=True) for item in checked
    ]}
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
