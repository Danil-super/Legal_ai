"""Closed, independently confirmed reports for factual-safety-intake.v1."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from legal_core.contracts import ContractModel

Signal = Literal["YES", "NO", "UNKNOWN"]
SCREENING_VERSION = "factual-safety-intake.v1"


class RequestedMoney(ContractModel):
    amount_kopecks: int = Field(alias="amountKopecks", strict=True, gt=0, le=100_000_000_000)
    currency: Literal["RUB"]


class FactualSafetyScreening(ContractModel):
    schema_version: Literal["factual-safety-intake.v1"] = Field(alias="schemaVersion")
    health_deterioration_reported: Signal = Field(alias="healthDeteriorationReported")
    hospitalization_reported: Signal = Field(alias="hospitalizationReported")
    representative_contact: Signal = Field(alias="representativeContact")
    written_requirements_received: Signal = Field(alias="writtenRequirementsReceived")
    authority_or_court_document_received: Signal = Field(alias="authorityOrCourtDocumentReceived")
    authority_referral_mentioned: Signal = Field(alias="authorityReferralMentioned")
    money_requested: Signal = Field(alias="moneyRequested")
    amount: RequestedMoney | Literal["UNKNOWN", "NOT_REQUESTED"]

    @model_validator(mode="after")
    def validate_requested_amount(self) -> FactualSafetyScreening:
        if self.money_requested == "NO" and self.amount != "NOT_REQUESTED":
            raise ValueError("no monetary request requires NOT_REQUESTED amount")
        if self.money_requested == "UNKNOWN" and self.amount != "UNKNOWN":
            raise ValueError("unknown monetary request requires UNKNOWN amount")
        if self.money_requested == "YES" and self.amount == "NOT_REQUESTED":
            raise ValueError("monetary request requires an explicit amount or UNKNOWN")
        return self
