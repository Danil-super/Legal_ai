"""Bounded reviewed examples, explicitly separate from facts and legal evidence."""

import hashlib
import json
from collections.abc import Sequence
from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from legal_core.pseudonymization import pseudonymize_text

MAX_REFERENCE_EXAMPLES = 3
MAX_REFERENCE_CHARACTERS = 12_000


class _AnalogyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True,
                              hide_input_in_errors=True, revalidate_instances="always", frozen=True)


class ReferenceAnalogySource(_AnalogyModel):
    reference_case_id: UUID = Field(alias="referenceCaseId")
    version_id: UUID = Field(alias="versionId")
    version: int = Field(strict=True, ge=1)
    approval_event_id: UUID = Field(alias="approvalEventId")
    scenario_sha256: str = Field(alias="scenarioSha256", pattern=r"^[0-9a-f]{64}$")
    group_key: Literal["clinical", "labour", "courts", "privacy", "licensing",
                       "healthcare", "general"] = Field(alias="groupKey")
    evaluation_as_of_date: date = Field(alias="evaluationAsOfDate")


class ReferenceAnalogyExample(_AnalogyModel):
    purpose: Literal["NON_AUTHORITATIVE_ANALOGY"] = "NON_AUTHORITATIVE_ANALOGY"
    date_basis: Literal["EVALUATION_DATE_NOT_EVENT_DATE"] = Field(
        default="EVALUATION_DATE_NOT_EVENT_DATE", alias="dateBasis",
    )
    source: ReferenceAnalogySource
    scenario_text: str = Field(alias="scenarioText", min_length=10,
                               max_length=MAX_REFERENCE_CHARACTERS)

    @model_validator(mode="after")
    def exact_deidentified_source(self) -> Self:
        if (not self.scenario_text.strip()
                or hashlib.sha256(self.scenario_text.encode()).hexdigest()
                != self.source.scenario_sha256
                or pseudonymize_text(self.scenario_text).changed):
            raise ValueError("analogy must match its exact de-identified source")
        return self


def reference_analogy_trace_sha256(examples: Sequence[ReferenceAnalogyExample]) -> str:
    checked = [ReferenceAnalogyExample.model_validate(item) for item in examples]
    wire = [item.model_dump(mode="json", by_alias=True) for item in checked]
    return hashlib.sha256(json.dumps(wire, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class ReferenceAnalogyContext(_AnalogyModel):
    examples: list[ReferenceAnalogyExample] = Field(max_length=MAX_REFERENCE_EXAMPLES)
    trace_sha256: str = Field(alias="traceSha256", pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def complete_unique_trace(self) -> Self:
        if (len({item.source.reference_case_id for item in self.examples}) != len(self.examples)
                or sum(len(item.scenario_text) for item in self.examples)
                > MAX_REFERENCE_CHARACTERS
                or self.trace_sha256 != reference_analogy_trace_sha256(self.examples)):
            raise ValueError("analogy selection exceeds its budget or differs from its trace")
        return self


def build_reference_analogy_context(
    examples: Sequence[ReferenceAnalogyExample],
) -> ReferenceAnalogyContext:
    return ReferenceAnalogyContext(examples=list(examples),
                                  traceSha256=reference_analogy_trace_sha256(examples))
