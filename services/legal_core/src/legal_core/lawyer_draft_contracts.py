"""Private metadata/context contracts; neither a verified answer nor an approval command."""

from typing import Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from legal_core.analysis_contracts import AnalysisContextResponse
from legal_core.case_preparation_contracts import PreparationModel, PreparationSource, Sha256


class LawyerDraftContextRequest(PreparationModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    expected_preparation_operation_id: UUID = Field(alias="expectedPreparationOperationId")
    expected_fact_snapshot_sha256: Sha256 = Field(alias="expectedFactSnapshotSha256")
    expected_risk_policy_version: str = Field(alias="expectedRiskPolicyVersion", strict=True,
                                             min_length=1, max_length=80)

    @field_validator("expected_risk_policy_version")
    @classmethod
    def nonblank_policy(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("risk policy version must not be blank")
        return value


class LawyerDraftContextResponse(PreparationModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal["lawyer-draft-context.v1"] = Field(alias="schemaVersion")
    source: PreparationSource
    escalation_id: UUID = Field(alias="escalationId")
    risk_level: Literal["HIGH", "CRITICAL"] = Field(alias="riskLevel")
    context: AnalysisContextResponse

    @model_validator(mode="after")
    def source_matches_context(self) -> Self:
        bound = self.source.context
        context = self.context
        if (bound is None or self.source.case_id != context.case_id
                or self.source.fact_snapshot_sha256 != context.fact_snapshot_sha256
                or bound.as_of_date != context.as_of_date
                or bound.evidence_trace_sha256 != context.evidence_trace_sha256
                or bound.clinic_document_context_trace_sha256
                != context.clinic_document_context_trace_sha256
                or bound.risk_policy_version != context.risk_policy_version):
            raise ValueError("lawyer draft source does not match its actual context")
        return self
