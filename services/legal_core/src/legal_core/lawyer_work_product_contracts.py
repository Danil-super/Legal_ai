"""Claim submission only; Core chooses storage identity and owns all verification."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from legal_core.analysis_contracts import AnalysisClaimInput, SemanticReviewInput
from legal_core.case_preparation_contracts import (
    InternalWorkProduct,
    PreparationModel,
    PreparationSource,
    Sha256,
)
from legal_core.contracts import LegalFragmentResponse
from legal_core.pseudonymization import pseudonymize_text


class LawyerWorkProductSubmission(PreparationModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    expected_source: PreparationSource = Field(alias="expectedSource")
    claims: list[AnalysisClaimInput] = Field(max_length=30)
    semantic_reviews: list[SemanticReviewInput] = Field(alias="semanticReviews", max_length=30)

    @model_validator(mode="after")
    def bounded_unapproved_claims(self) -> Self:
        if self.expected_source.context is None:
            raise ValueError("a lawyer draft requires its actual retrieved source context")
        identities = [item.claim_id for item in self.claims]
        reviewed = [item.claim_id for item in self.semantic_reviews]
        if (len(set(identities)) != len(identities) or len(set(reviewed)) != len(reviewed)
                or not set(reviewed) <= set(identities)):
            raise ValueError("claim and review identities must be unique and match")
        actions = [item for item in self.claims if item.kind == "ACTION"]
        if len(actions) > 8 or any(len(item.text) > 600 for item in actions):
            raise ValueError("a draft permits at most eight bounded action claims")
        if any(not item.text.strip() or pseudonymize_text(item.text).changed
               for item in self.claims):
            raise ValueError("draft claims must be nonblank de-identified text")
        return self


class LawyerWorkProductView(PreparationModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    version_id: UUID = Field(alias="versionId")
    review_state: Literal["UNAPPROVED", "HUMAN_REVISED", "HUMAN_APPROVED"] = Field(
        alias="reviewState",
    )
    origin: Literal["AI", "HUMAN"]
    is_current: bool = Field(alias="isCurrent", strict=True)
    content_sha256: Sha256 = Field(alias="contentSha256")
    content: InternalWorkProduct
    citations: list[LegalFragmentResponse] = Field(max_length=30)

    @model_validator(mode="after")
    def immutable_content_binding(self) -> Self:
        if self.content_sha256 != self.content.content_sha256:
            raise ValueError("work-product view differs from its retained content")
        if (self.origin == "AI") != (self.review_state == "UNAPPROVED"):
            raise ValueError("review state must match the real version origin")
        cited = [item.fragment_id for item in self.citations]
        permitted = set(self.content.legal_fragment_ids)
        if (len(cited) != len(set(cited)) or not set(cited) <= permitted
                or (self.is_current and set(cited) != permitted)):
            raise ValueError("work-product citations do not match retained legal references")
        return self


BoundedStep = Annotated[str, Field(strict=True, min_length=1, max_length=600)]


class LawyerWorkProductReview(PreparationModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    expected_version_id: UUID = Field(alias="expectedVersionId")
    expected_content_sha256: Sha256 = Field(alias="expectedContentSha256")
    decision: Literal["APPROVE", "REVISE"]
    summary: str | None = Field(default=None, strict=True, min_length=1, max_length=1500)
    actions: list[BoundedStep] | None = Field(default=None, min_length=1, max_length=8)
    uncertainties: list[BoundedStep] | None = Field(default=None, max_length=5)

    @model_validator(mode="after")
    def explicit_review_decision(self) -> Self:
        changes = (self.summary is not None or self.actions is not None
                   or self.uncertainties is not None)
        if (self.decision == "REVISE") != changes:
            raise ValueError("approve the displayed version or explicitly revise its content")
        text = [*(self.actions or []), *(self.uncertainties or [])]
        if self.summary is not None:
            text.append(self.summary)
        if any(not item.strip() or pseudonymize_text(item).changed for item in text):
            raise ValueError("reviewed content must be nonblank de-identified text")
        return self
