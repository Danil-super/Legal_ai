"""Strict public understanding boundary using the existing authenticated Core client."""

import hashlib
import json
from typing import Any
from uuid import UUID

from legal_core.contracts import validate_intake_draft_data
from legal_core.intake_preparation_contracts import PreparationStatusResponse

from telegram_gateway.case_wizard import LegalCoreApiError


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def validated_draft(payload: dict[str, Any], draft_id: UUID) -> dict[str, Any]:
    if (payload.get("id") != str(draft_id) or type(payload.get("revision")) is not int
            or payload["revision"] < 1 or not isinstance(payload.get("wizardState"), str)
            or not isinstance(payload.get("draftData"), dict)
            or payload["draftData"].get("intakeVersion") != 2):
        raise ValueError("invalid understanding draft")
    validate_intake_draft_data(payload["draftData"])
    return payload


class UnderstandingClient:
    def __init__(self, core: Any, actor: int) -> None:
        self.core, self.actor = core, actor

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        failed = False
        try:
            return await self.core._json_request(
                method, path, telegram_user_id=self.actor, **kwargs,
            )
        except (LegalCoreApiError, KeyError, TypeError, ValueError):
            failed = True
        if failed:
            # Raise outside except: no transport/Pydantic input retained in exception context.
            raise LegalCoreApiError(502, "UNDERSTANDING_UNAVAILABLE", "Result unavailable")
        raise AssertionError("unreachable")

    def _status(self, value: dict[str, Any], *, operation: UUID | None = None,
                draft_id: UUID | None = None) -> PreparationStatusResponse:
        failed = False
        try:
            if set(value) != {"operationId", "state", "suggestions", "source", "acceptance"}:
                raise ValueError("invalid public status shape")
            status = PreparationStatusResponse.model_validate(value)
            if operation is not None and status.operation_id != operation:
                raise ValueError("operation mismatch")
            source = status.source or status.acceptance
            if draft_id is not None and source is not None and source.draft_id != draft_id:
                raise ValueError("draft mismatch")
            return status
        except (ValueError, TypeError, AttributeError):
            failed = True
        if failed:
            raise LegalCoreApiError(502, "INVALID_UNDERSTANDING_RESPONSE", "Result unavailable")
        raise AssertionError("unreachable")

    async def start(self, draft_id: UUID, revision: int) -> PreparationStatusResponse:
        return self._status(await self._request(
            "POST", f"/v1/telegram-intake-drafts/{draft_id}/understanding",
            payload={"expectedRevision": revision},
        ), draft_id=draft_id)

    async def get(self, operation: UUID) -> PreparationStatusResponse:
        return self._status(await self._request(
            "GET", f"/v1/intake-preparations/{operation}",
        ), operation=operation)

    async def discover(self, draft_id: UUID) -> PreparationStatusResponse | None:
        value = await self._request("GET", f"/v1/telegram-intake-drafts/{draft_id}/understanding")
        if set(value) != {"operation"}:
            raise LegalCoreApiError(502, "INVALID_UNDERSTANDING_RESPONSE", "Result unavailable")
        if value["operation"] is None:
            return None
        return self._status(value["operation"], draft_id=draft_id)

    async def cancel(self, operation: UUID) -> PreparationStatusResponse:
        status = self._status(await self._request(
            "DELETE", f"/v1/intake-preparations/{operation}",
        ), operation=operation)
        if status.state != "MANUAL_CONTINUATION":
            raise LegalCoreApiError(409, "UNDERSTANDING_NOT_CANCELLED", "Result unavailable")
        return status

    async def proposal_draft(self, status: PreparationStatusResponse,
                             payload: dict[str, Any]) -> dict[str, Any]:
        failed = False
        try:
            source, suggestions = status.source, status.suggestions
            if source is None or suggestions is None or status.state != "PROPOSED":
                raise ValueError("no proposal")
            draft = validated_draft(payload, source.draft_id)
            proposed = {key: value for key, value in suggestions.model_dump(
                mode="json", by_alias=True,
            ).items() if value is not None}
            if (draft["revision"] != source.draft_revision
                    or canonical_hash({key: draft[key] for key in ("draftData", "wizardState")})
                    != source.input_sha256
                    or canonical_hash(proposed) != source.proposal_sha256):
                raise ValueError("proposal no longer matches draft")
            suggestions.validate_source(draft["draftData"].get("eventSummary", ""))
            return draft
        except (ValueError, KeyError, TypeError):
            failed = True
        if failed:
            raise LegalCoreApiError(409, "UNDERSTANDING_STALE", "Open the current draft")
        raise AssertionError("unreachable")

    async def accept(self, status: PreparationStatusResponse) -> dict[str, Any]:
        source = status.source
        if status.state != "PROPOSED" or source is None:
            raise LegalCoreApiError(409, "UNDERSTANDING_NOT_PROPOSED", "Open current draft")
        result = await self._request(
            "POST", f"/v1/intake-preparations/{status.operation_id}/acceptance", payload={
                "expectedRevision": source.draft_revision,
                "expectedInputSha256": source.input_sha256,
                "expectedProposalSha256": source.proposal_sha256,
            },
        )
        failed = False
        try:
            if (set(result) != {"operationId", "acceptedRevision", "draft"}
                    or result["operationId"] != str(status.operation_id)
                    or type(result["acceptedRevision"]) is not int
                    or result["acceptedRevision"] != source.draft_revision + 1):
                raise ValueError("invalid acceptance")
            draft = validated_draft(result["draft"], source.draft_id)
            if draft["revision"] < result["acceptedRevision"]:
                raise ValueError("old accepted draft")
            return draft
        except (ValueError, KeyError, TypeError):
            failed = True
        if failed:
            raise LegalCoreApiError(502, "INVALID_UNDERSTANDING_RESPONSE", "Result unavailable")
        raise AssertionError("unreachable")
