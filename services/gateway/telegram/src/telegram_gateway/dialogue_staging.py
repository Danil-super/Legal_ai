"""Provisional draft transport; only final summary confirmation creates case facts."""

from typing import Any
from uuid import UUID

from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.understanding_client import validated_draft

ENABLED = "intake_simplified_dialogue"


async def stage(client: Any, status: Any, draft: dict[str, Any], *, answer: bool) -> dict[str, Any]:
    """Apply one current, source-bound proposal without forging explicit acceptance.

    Core repeats grounding, ownership and revision validation atomically. The
    identical POST can be replayed after a lost response; never start the model
    again or infer staging success from a failed network request.
    """
    await client.proposal_draft(status, draft)
    source = status.source
    stageable = (status.interpretation is not None
                 and status.interpretation.has_provisional_facts) if answer else (
                     status.state == "PROPOSED")
    if not stageable or source is None:
        raise ValueError("no stageable proposal")
    prefix = "intake-answer-preparations" if answer else "intake-preparations"
    payload = {"expectedRevision": source.draft_revision,
               "expectedInputSha256": source.input_sha256,
               "expectedProposalSha256": source.proposal_sha256}
    result = None
    for _ in range(2):
        try:
            result = await client._request(
                "POST", f"/v1/{prefix}/{status.operation_id}/draft-staging", payload=payload,
            )
            break
        except LegalCoreApiError:
            continue
    if result is None:
        raise LegalCoreApiError(503, "DRAFT_STAGING_UNAVAILABLE", "Draft unavailable")
    if (set(result) != {"operationId", "stagedRevision", "draft"}
            or result["operationId"] != str(status.operation_id)
            or type(result["stagedRevision"]) is not int
            or result["stagedRevision"] != source.draft_revision + 1):
        raise ValueError("invalid staging receipt")
    saved = validated_draft(result["draft"], UUID(draft["id"]))
    if saved["revision"] < result["stagedRevision"]:
        raise ValueError("staging receipt newer than draft")
    return saved
