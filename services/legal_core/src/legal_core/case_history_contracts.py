"""Metadata-only history shared by Core and the Telegram client."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from legal_core.contracts import CaseResponse, ContractModel

PAGE_SIZE = 8


class CaseHistoryPage(ContractModel):
    items: list[CaseResponse] = Field(max_length=PAGE_SIZE)
    next_offset: int | None = Field(default=None, alias="nextOffset", ge=0)


class SavedReport(ContractModel):
    id: UUID
    report_version: int = Field(alias="reportVersion", ge=1)
    created_at: datetime = Field(alias="createdAt")
    kind: Literal["READY", "CARD"]


class CaseHistoryDetail(ContractModel):
    case: CaseResponse
    reports: list[SavedReport] = Field(max_length=PAGE_SIZE)
    next_offset: int | None = Field(default=None, alias="nextOffset", ge=0)
    escalation_id: UUID | None = Field(default=None, alias="escalationId")


class CaseHistoryDeletion(ContractModel):
    case_id: UUID = Field(alias="caseId")
    status: Literal["DELETION_SCHEDULED"] = "DELETION_SCHEDULED"
