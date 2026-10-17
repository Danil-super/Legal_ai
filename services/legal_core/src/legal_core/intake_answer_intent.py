"""Separate answer identity: never changes the deployed narrative-intent contract."""

import hashlib
import json
from datetime import date
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ConfigDict, Field

from legal_core.case_preparation_contracts import PreparationModel, Sha256


class AnswerPreparationIntent(PreparationModel):
    """Server-built identity only, not an authorization or a confirmed answer."""

    model_config = ConfigDict(frozen=True)
    schema_version: Literal["intake-answer-intent.v1"] = Field(
        default="intake-answer-intent.v1", alias="schemaVersion",
    )
    operation_kind: Literal["INTERPRET_ANSWER"] = Field(
        default="INTERPRET_ANSWER", alias="operationKind",
    )
    clinic_id: UUID = Field(alias="clinicId")
    actor_membership_id: UUID = Field(alias="actorMembershipId")
    draft_id: UUID = Field(alias="draftId")
    draft_revision: int = Field(alias="draftRevision", strict=True, ge=1)
    input_sha256: Sha256 = Field(alias="inputSha256")
    answer_sha256: Sha256 = Field(alias="answerSha256")
    question_sha256: Sha256 = Field(alias="questionSha256")
    as_of_date: date = Field(alias="asOfDate")

    @property
    def scope(self) -> str:
        return f"draft-answer:v1:{self.draft_id}"

    @property
    def request_sha256(self) -> str:
        payload = json.dumps(self.model_dump(mode="json", by_alias=True),
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def idempotency_key(self) -> UUID:
        return uuid5(NAMESPACE_URL, f"{self.scope}:{self.request_sha256}")
