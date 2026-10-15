"""Bounded preparation data, never model-authored legal authority or permissions."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ConfigDict, Field, field_validator, model_validator

from legal_core.analysis_clarifications import ClarificationRequest, validate_unique_requests
from legal_core.contracts import ContractModel, FactKey

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PreparationModel(ContractModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, revalidate_instances="always",
    )


class DraftPreparationIntent(PreparationModel):
    """Server-built identity; constructing it does not authorize an actor or reserve work."""

    model_config = ConfigDict(frozen=True)
    clinic_id: UUID = Field(alias="clinicId")
    actor_membership_id: UUID = Field(alias="actorMembershipId")
    draft_id: UUID = Field(alias="draftId")
    draft_revision: int = Field(alias="draftRevision", strict=True, ge=1)
    input_sha256: Sha256 = Field(alias="inputSha256")
    operation_kind: Literal["UNDERSTAND_INTAKE", "INTERPRET_ANSWER"] = Field(alias="operationKind")

    @property
    def scope(self) -> str:
        return f"draft-prep:v1:{self.draft_id}"

    @property
    def request_sha256(self) -> str:
        payload = json.dumps(self.model_dump(mode="json", by_alias=True), sort_keys=True,
                             separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def idempotency_key(self) -> UUID:
        return uuid5(NAMESPACE_URL, f"{self.scope}:{self.request_sha256}")


class PreparationContext(PreparationModel):
    as_of_date: date = Field(alias="asOfDate")
    evidence_trace_sha256: Sha256 = Field(alias="evidenceTraceSha256")
    clinic_document_context_trace_sha256: Sha256 = Field(alias="clinicDocumentContextTraceSha256")
    risk_policy_version: str = Field(alias="riskPolicyVersion", min_length=1, max_length=80)

    @field_validator("risk_policy_version")
    @classmethod
    def policy_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("risk policy version must not be blank")
        return value


class PreparationSource(PreparationModel):
    operation_id: UUID = Field(alias="operationId")
    case_id: UUID = Field(alias="caseId")
    fact_snapshot_sha256: Sha256 = Field(alias="factSnapshotSha256")
    context: PreparationContext | None


class PreparationResultBase(PreparationModel):
    result_version: Literal[2] = Field(alias="resultVersion")
    source: PreparationSource

    @field_validator("result_version", mode="before")
    @classmethod
    def version_is_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("result version must be an integer")
        return value


class NeedsFactsResult(PreparationResultBase):
    status: Literal["NEEDS_FACTS"]
    reason_code: Literal["FACTUAL_CLARIFICATION_REQUIRED"] = Field(alias="reasonCode")
    clarification_requests: list[ClarificationRequest] = Field(
        default_factory=list, alias="clarificationRequests", max_length=3,
    )
    missing_fact_keys: list[FactKey] = Field(
        default_factory=list, alias="missingFactKeys", max_length=3,
    )

    @field_validator("clarification_requests", mode="before")
    @classmethod
    def revalidate_requests(cls, value: object) -> object:
        if isinstance(value, list):
            return [
                item.model_dump(by_alias=True) if isinstance(item, ClarificationRequest) else item
                for item in value
            ]
        return value

    @model_validator(mode="after")
    def bounded_unique_questions(self) -> NeedsFactsResult:
        validate_unique_requests(self.clarification_requests)
        if len(set(self.missing_fact_keys)) != len(self.missing_fact_keys):
            raise ValueError("missing fact keys must be unique")
        if not 1 <= len(self.clarification_requests) + len(self.missing_fact_keys) <= 3:
            raise ValueError("preparation requires between one and three factual questions")
        return self


class LimitedResult(PreparationResultBase):
    status: Literal["LIMITED"]
    reason_code: Literal[
        "INSUFFICIENT_FACTS", "ANALYSIS_DATE_UNCERTAIN", "LEGAL_EVIDENCE_UNAVAILABLE",
        "EVIDENCE_NOT_VERIFIED", "FACTUAL_SAFETY_UNCERTAIN",
    ] = Field(alias="reasonCode")
    missing_fact_keys: list[FactKey] = Field(
        default_factory=list, alias="missingFactKeys", max_length=20,
    )

    @field_validator("missing_fact_keys")
    @classmethod
    def unique_keys(cls, value: list[FactKey]) -> list[FactKey]:
        if len(value) != len(set(value)):
            raise ValueError("missing fact keys must be unique")
        return value


class HumanReviewResult(PreparationResultBase):
    status: Literal["HUMAN_REVIEW"]
    escalation_id: UUID = Field(alias="escalationId")
    risk_level: Literal["HIGH", "CRITICAL"] = Field(alias="riskLevel")
    risk_policy_version: str = Field(alias="riskPolicyVersion", min_length=1, max_length=80)

    @model_validator(mode="after")
    def coherent_policy(self) -> HumanReviewResult:
        if not self.risk_policy_version.strip():
            raise ValueError("risk policy version must not be blank")
        if (
            self.source.context
            and self.source.context.risk_policy_version != self.risk_policy_version
        ):
            raise ValueError("escalation policy must match the bound context")
        return self
