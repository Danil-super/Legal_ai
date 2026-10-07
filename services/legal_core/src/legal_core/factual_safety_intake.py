"""Closed, independently confirmed reports for factual-safety-intake.v1."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationError, model_validator

from legal_core.contracts import ContractModel

Signal = Literal["YES", "NO", "UNKNOWN"]
SCREENING_VERSION = "factual-safety-intake.v1"

SCREENING_FIELDS = (
    "healthDeteriorationReported",
    "hospitalizationReported",
    "representativeContact",
    "writtenRequirementsReceived",
    "authorityOrCourtDocumentReceived",
    "authorityReferralMentioned",
    "moneyRequested",
)


def validate_partial_screening(value: object) -> dict[str, object]:
    """Drafts may be incomplete, never unversioned, open-ended or mistyped."""
    if (
        not isinstance(value, dict)
        or not set(value)
        <= {
            "schemaVersion",
            "amount",
            *SCREENING_FIELDS,
        }
        or value.get("schemaVersion") != SCREENING_VERSION
    ):
        raise ValueError("invalid factual screening draft shape")
    if any(
        not isinstance(value[field], str) or value[field] not in {"YES", "NO", "UNKNOWN"}
        for field in SCREENING_FIELDS
        if field in value
    ):
        raise ValueError("invalid factual screening draft signal")
    if "amount" in value:
        amount = value["amount"]
        if not isinstance(amount, str):
            try:
                RequestedMoney.model_validate(amount)
            except ValidationError as exc:
                raise ValueError("invalid factual screening draft amount") from exc
        elif amount not in {"UNKNOWN", "NOT_REQUESTED"}:
            raise ValueError("invalid factual screening draft amount")
        signal = value.get("moneyRequested")
        if (
            (signal == "NO" and amount != "NOT_REQUESTED")
            or (signal == "UNKNOWN" and amount != "UNKNOWN")
            or (signal == "YES" and amount == "NOT_REQUESTED")
            or signal is None
        ):
            raise ValueError("factual screening draft monetary fields conflict")
    return value


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
