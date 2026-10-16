"""Strict subsequent-answer wire facade; never sends a client-chosen question or fact."""

from typing import Any
from uuid import UUID

from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_answer_values import validate_answer_shape
from legal_core.intake_answer_wire import (
    AnswerDiscoveryResponse,
    AnswerStatusResponse,
    StartAnswerRequest,
)

from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.understanding_client import canonical_hash, validated_draft


class AnswerClient:
    def __init__(self, core: Any, actor: int) -> None:
        self.core, self.actor = core, actor

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            value = await self.core._json_request(method, path, telegram_user_id=self.actor,
                                                  **kwargs)
            if isinstance(value, dict):
                return value
        except (LegalCoreApiError, ValueError, TypeError, KeyError):
            pass
        # Outside except: raw answer and transport/Pydantic exception contexts are not retained.
        raise LegalCoreApiError(502, "ANSWER_UNAVAILABLE", "Result unavailable")

    @staticmethod
    def _status(value: object, *, operation: UUID | None = None,
                draft_id: UUID | None = None) -> AnswerStatusResponse:
        try:
            if not isinstance(value, dict) or set(value) != {
                "operationId", "state", "question", "interpretation", "source", "acceptance",
            }:
                raise ValueError("invalid public answer shape")
            status = AnswerStatusResponse.model_validate(value)
            if operation is not None and status.operation_id != operation:
                raise ValueError("operation mismatch")
            binding = status.source or status.acceptance
            if draft_id is not None and binding is not None and binding.draft_id != draft_id:
                raise ValueError("draft mismatch")
            if (status.question is not None and status.interpretation is not None
                    and status.interpretation.status == "PROPOSED"):
                validate_answer_shape(status.question, status.interpretation.value)
            return status
        except (ValueError, TypeError, AttributeError):
            pass
        raise LegalCoreApiError(502, "INVALID_ANSWER_RESPONSE", "Result unavailable")

    async def start(self, draft_id: UUID, revision: int, answer: str) -> AnswerStatusResponse:
        try:
            payload = StartAnswerRequest(expectedRevision=revision, answer=answer).model_dump(
                mode="json", by_alias=True,
            )
        except (ValueError, TypeError):
            payload = None
        if payload is None:
            raise LegalCoreApiError(422, "INVALID_ANSWER", "Invalid answer")
        return self._status(await self._request(
            "POST", f"/v1/telegram-intake-drafts/{draft_id}/answer-interpretations",
            payload=payload,
        ), draft_id=draft_id)

    async def get(self, operation: UUID) -> AnswerStatusResponse:
        return self._status(await self._request(
            "GET", f"/v1/intake-answer-preparations/{operation}",
        ), operation=operation)

    async def discover(self, draft_id: UUID) -> AnswerStatusResponse | None:
        value = await self._request(
            "GET", f"/v1/telegram-intake-drafts/{draft_id}/answer-interpretations",
        )
        invalid = False
        try:
            AnswerDiscoveryResponse.model_validate(value)
            if set(value) != {"operation"}:
                raise ValueError("invalid discovery shape")
            if value["operation"] is None:
                return None
        except (ValueError, TypeError):
            invalid = True
        if invalid:
            raise LegalCoreApiError(502, "INVALID_ANSWER_RESPONSE", "Result unavailable")
        return self._status(value["operation"], draft_id=draft_id)

    async def cancel(self, operation: UUID) -> AnswerStatusResponse:
        status = self._status(await self._request(
            "DELETE", f"/v1/intake-answer-preparations/{operation}",
        ), operation=operation)
        if status.state != "MANUAL_CONTINUATION":
            raise LegalCoreApiError(409, "ANSWER_NOT_CANCELLED", "Result unavailable")
        return status

    async def proposal_draft(self, status: AnswerStatusResponse,
                             payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source, interpretation = status.source, status.interpretation
            if (source is None or interpretation is None or status.question is None
                    or status.state not in {"PROPOSED", "UNRESOLVED"}):
                raise ValueError("no interpretation")
            draft = validated_draft(payload, source.draft_id)
            if (draft["revision"] != source.draft_revision
                    or canonical_hash({key: draft[key] for key in ("draftData", "wizardState")})
                    != source.input_sha256
                    or canonical_hash(interpretation.model_dump(mode="json", by_alias=True))
                    != source.proposal_sha256
                    or derive_answer_question(draft["draftData"], draft["wizardState"])
                    != status.question):
                raise ValueError("answer proposal no longer matches current question")
            return draft
        except (ValueError, KeyError, TypeError):
            pass
        raise LegalCoreApiError(409, "ANSWER_STALE", "Open current draft")

    async def accept(self, status: AnswerStatusResponse) -> dict[str, Any]:
        source = status.source
        if status.state != "PROPOSED" or source is None:
            raise LegalCoreApiError(409, "ANSWER_NOT_PROPOSED", "Open current draft")
        result = await self._request(
            "POST", f"/v1/intake-answer-preparations/{status.operation_id}/acceptance", payload={
                "expectedRevision": source.draft_revision,
                "expectedInputSha256": source.input_sha256,
                "expectedProposalSha256": source.proposal_sha256,
            },
        )
        try:
            if (set(result) != {"operationId", "acceptedRevision", "draft"}
                    or result["operationId"] != str(status.operation_id)
                    or type(result["acceptedRevision"]) is not int
                    or result["acceptedRevision"] != source.draft_revision + 1):
                raise ValueError("invalid acceptance receipt")
            draft = validated_draft(result["draft"], source.draft_id)
            if draft["revision"] < result["acceptedRevision"]:
                raise ValueError("old accepted snapshot")
            return draft
        except (ValueError, TypeError, KeyError):
            pass
        raise LegalCoreApiError(502, "INVALID_ANSWER_RESPONSE", "Result unavailable")
