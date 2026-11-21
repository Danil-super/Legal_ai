"""Persist user-authored narrative before any optional background interpretation."""

from copy import deepcopy
from typing import Any
from uuid import UUID

from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.understanding_client import validated_draft
from telegram_gateway.understanding_intent import source_keys


class SourceSaveError(LegalCoreApiError):
    def __init__(self, status_code: int, code: str, draft_id: UUID | None) -> None:
        super().__init__(status_code, code, "Source save could not be confirmed")
        self.draft_id = draft_id


async def _write(core: Any, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    # One bounded transport retry uses the EXACT original body/key, never a new intent.
    for _ in range(2):
        try:
            result = await core._json_request(method, path, **kwargs)
            if not isinstance(result, dict):
                raise ValueError("invalid draft write response")
            return result
        except LegalCoreApiError as error:
            if error.status_code < 500:
                raise
    raise LegalCoreApiError(503, "SOURCE_SAVE_UNCONFIRMED", "Open the saved drafts")


async def save_source(
    core: Any, actor: int, chat: int, message: int, summary: str,
    *, edit_draft_id: UUID | None = None, create_message_id: int | None = None,
) -> dict[str, Any]:
    """One source event, one owned draft; never persist suggested structured facts."""
    draft_id = edit_draft_id
    try:
        create_key, save_key = source_keys(actor, chat, message)
        if draft_id is None:
            if create_message_id is not None:
                create_key, _ = source_keys(actor, chat, create_message_id)
            created = await _write(
                core, "POST", "/v1/telegram-intake-drafts", telegram_user_id=actor,
                idempotency_key=create_key,
                payload={"intakeSchemaVersion": "dental-case-intake.v2"},
            )
            created_id = UUID(created["id"])
            validated_draft(created, created_id)
            draft_id = created_id
        path = f"/v1/telegram-intake-drafts/{draft_id}"
        # Creation replay can be an old snapshot. Current owned data always wins.
        current = validated_draft(await core.get_intake_draft(draft_id, actor), draft_id)
        payload = {"intakeVersion": 2, "eventSummary": summary}
        saved_data = current["draftData"]
        if set(saved_data) - {"intakeVersion", "eventSummary", "incomingSourceStatus"}:
            raise ValueError("source event cannot replace later confirmed answers")
        if "incomingSourceStatus" in saved_data:
            if saved_data["incomingSourceStatus"] not in {
                "NOT_ATTACHED", "TEXT_ATTACHED", "FILE_ATTACHED",
            }:
                raise ValueError("invalid attachment metadata")
            payload["incomingSourceStatus"] = saved_data["incomingSourceStatus"]
        elif current["revision"] == 1 and saved_data == {"intakeVersion": 2}:
            # This newly created empty draft has no attachment. This is observed
            # application metadata, not a model-suggested fact about the patient.
            payload["incomingSourceStatus"] = "NOT_ATTACHED"
        if saved_data == payload:
            return current
        if "eventSummary" in saved_data and edit_draft_id is None:
            raise ValueError("source replay does not match its original narrative")
        body = {"expectedRevision": current["revision"], "wizardState": "INCOMING",
                "draftData": payload}
        try:
            saved = validated_draft(await _write(
                core, "PUT", path, telegram_user_id=actor, idempotency_key=save_key,
                payload=deepcopy(body),
            ), draft_id)
        except LegalCoreApiError as error:
            if error.status_code < 500:
                raise
        except (KeyError, TypeError, ValueError):
            pass
        else:
            if _matches_write(saved, current["revision"], payload):
                return saved
        # A timeout or invalid acknowledgement can hide a successful commit. Prove the exact
        # revision and payload via the authenticated read; never acknowledge just any draft.
        saved = validated_draft(await core.get_intake_draft(draft_id, actor), draft_id)
        if _matches_write(saved, current["revision"], payload):
            return saved
    except LegalCoreApiError as error:
        status, code = error.status_code, error.code
    except (KeyError, TypeError, ValueError):
        status, code = 503, "SOURCE_SAVE_UNCONFIRMED"
    else:
        status, code = 503, "SOURCE_SAVE_UNCONFIRMED"
    raise SourceSaveError(status, code, draft_id)


def _matches_write(saved: dict[str, Any], revision: int, payload: dict[str, Any]) -> bool:
    return (saved["revision"], saved["wizardState"], saved["draftData"]) == (
        revision + 1, "INCOMING", payload,
    )
