"""Private current-answer context and fenced unconfirmed model completion."""

import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_clarifications import confirmed_answers
from legal_core.analysis_safe_stop import require_analysis_running
from legal_core.case_api import ApiError, _audit
from legal_core.contracts import validate_intake_draft_data
from legal_core.intake_answer_contracts import AnswerInterpretation
from legal_core.intake_answer_operations import (
    AnswerOperation,
    checked_result,
    proposal_metadata,
    stored_answer,
)
from legal_core.intake_answer_values import validate_answer_value
from legal_core.intake_answer_wire import CONTEXT_FIELDS, AnswerContext
from legal_core.intake_factual_candidates import apply_factual_answer
from legal_core.intake_preparation import RESOURCE_TYPE, _locked_owned_operation
from legal_core.intake_preparation_contracts import DraftSuggestions
from legal_core.models import IdempotencyRecord, TelegramIntakeDraft
from legal_core.pseudonymization import pseudonymize_text


def _live_lease(record: IdempotencyRecord, lease_token: UUID) -> None:
    if (record.state != "IN_PROGRESS" or record.lease_token != lease_token
            or record.lease_until is None or record.lease_until <= datetime.now(UTC)):
        raise ApiError(status_code=409, code="ANSWER_LEASE_EXPIRED",
                       message="The answer operation lease is no longer current")


def _context(draft: TelegramIntakeDraft) -> dict[str, JsonValue]:
    # The allowlist excludes material text/files, correction origins, actor and storage IDs.
    context = {key: value for key, value in draft.draft_json.items() if key in CONTEXT_FIELDS}
    try:
        validate_intake_draft_data(context)
        if type(context.get("intakeVersion")) is not int or context["intakeVersion"] != 2:
            raise ValueError("invalid context version")
        fields = {key: value for key, value in context.items() if key in {
            field.alias for field in DraftSuggestions.model_fields.values()
        } and key != "sourceSpans"}
        # Reuse closed factual field shapes only; these placeholder offsets prove no evidence.
        DraftSuggestions.model_validate(fields | {
            "sourceSpans": {key: [{"start": 0, "end": 1}] for key in fields},
        })
        for field, bounds in (("eventSummary", (10, 1500)), ("clinicActionsNote", (2, 500))):
            if field in context and (
                not isinstance(context[field], str)
                or not bounds[0] <= len(context[field].strip()) <= bounds[1]
            ):
                raise ValueError("invalid factual context text")
        for field, choices in (
            ("incomingSourceStatus", {"NOT_ATTACHED", "TEXT_ATTACHED", "FILE_ATTACHED"}),
            ("caseMaterialsStatus", {"NOT_ATTACHED", "ATTACHED"}),
        ):
            if field in context and (
                not isinstance(context[field], str) or context[field] not in choices
            ):
                raise ValueError("invalid factual context status")
        confirmed_answers({
            "INTAKE_VERSION": "GUIDED_V2", "SITUATION_AREAS": context.get("situationAreas"),
            "AFFECTED_SERVICES": context.get("affectedServices"),
            "ANALYSIS_CLARIFICATION_ANSWERS": context.get("analysisClarificationAnswers",
                                                         {"answers": []}),
        })
        # Do not redact context: this boundary must never change factual meanings silently.
        if pseudonymize_text(json.dumps(context, ensure_ascii=False, allow_nan=False)).changed:
            raise ValueError("identifying context")
        return context
    except (ValueError, TypeError):
        pass
    raise ApiError(status_code=422, code="ANSWER_CONTEXT_INVALID",
                   message="The current factual context is not valid")


async def read_answer_context(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, operation_id: UUID,
    lease_token: UUID, expected_revision: int, expected_input_sha256: str,
) -> AnswerContext:
    require_analysis_running()
    async with sessions() as session:
        actor, draft, record = await _locked_owned_operation(
            session, telegram_user_id, operation_id,
        )
        stored = stored_answer(actor, draft, record)
        _live_lease(record, lease_token)
        if (type(expected_revision) is not int or expected_revision != draft.revision
                or expected_input_sha256 != stored.intent.input_sha256):
            raise ApiError(status_code=409, code="ANSWER_INPUT_STALE",
                           message="The answer draft binding has changed")
        require_analysis_running()
        result = AnswerContext(
            operationId=record.id, draftId=draft.id, draftRevision=draft.revision,
            inputSha256=stored.intent.input_sha256, answerSha256=stored.intent.answer_sha256,
            questionSha256=stored.intent.question_sha256, asOfDate=stored.intent.as_of_date,
            question=stored.question, answer=stored.answer, confirmedContext=_context(draft),
            retainedSources=stored.retained_sources, allowedQuestions=stored.allowed_questions,
        )
        session.add(_audit(actor=actor, action="INTAKE_ANSWER_CONTEXT_READ",
                           resource_type=RESOURCE_TYPE, resource_id=record.id,
                           metadata={"revision": draft.revision,
                                     "questionSha256": stored.intent.question_sha256}))
        await session.commit()
        return result


async def complete_answer(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, operation_id: UUID,
    lease_token: UUID, interpretation: AnswerInterpretation,
) -> AnswerOperation:
    async with sessions() as session:
        actor, draft, record = await _locked_owned_operation(
            session, telegram_user_id, operation_id,
        )
        stored = stored_answer(actor, draft, record)
        _live_lease(record, lease_token)
        checked = None
        try:
            proposal = AnswerInterpretation.model_validate(interpretation.model_dump(by_alias=True))
            proposal.validate_source(stored.answer)
            if proposal.status == "PROPOSED":
                value = validate_answer_value(stored.question, proposal.value, stored.answer,
                                              draft.draft_json, as_of_date=stored.intent.as_of_date)
                if stored.question.wizard_state == "SERVICES" and isinstance(value, list) and any(
                    not isinstance(service, str) or not any(
                        service in stored.answer[span.start:span.end]
                        for span in proposal.source_spans
                    ) for service in value
                ):
                    raise ValueError("service labels are not in their actual answer anchors")
                proposal = AnswerInterpretation.model_validate({
                    **proposal.model_dump(by_alias=True), "value": value,
                })
            if proposal.factual_proposal is not None:
                if stored.retained_sources is None:
                    raise ValueError("retained sources were not reserved for this operation")
                apply_factual_answer(draft.draft_json, stored.question, proposal, stored.answer,
                                     stored.retained_sources,
                                     as_of_date=stored.intent.as_of_date)
            checked = proposal
        except (ValueError, TypeError, AttributeError):
            pass
        if checked is None:
            raise ApiError(status_code=422, code="ANSWER_PROPOSAL_INVALID",
                           message="The model answer proposal is not valid")
        stored.interpretation = checked
        record.response_json = stored.model_dump(mode="json", by_alias=True)
        record.state, record.lease_token, record.lease_until = "SUCCEEDED", None, None
        session.add(_audit(actor=actor, action="INTAKE_ANSWER_PROPOSED",
                           resource_type=RESOURCE_TYPE, resource_id=record.id,
                           metadata=proposal_metadata(stored)))
        await session.flush()
        result = await checked_result(session, actor, draft, record)
        await session.commit()
        return result


async def fail_answer(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, operation_id: UUID,
    lease_token: UUID, reason_code: Literal[
        "MODEL_TIMEOUT", "MODEL_UNAVAILABLE", "MODEL_PROTOCOL_INVALID",
    ],
) -> AnswerOperation:
    if reason_code not in {"MODEL_TIMEOUT", "MODEL_UNAVAILABLE", "MODEL_PROTOCOL_INVALID"}:
        raise ValueError("unsupported answer failure reason")
    async with sessions() as session:
        actor, draft, record = await _locked_owned_operation(
            session, telegram_user_id, operation_id,
        )
        stored_answer(actor, draft, record)
        _live_lease(record, lease_token)
        record.state, record.lease_token, record.lease_until = "FAILED", None, None
        session.add(_audit(actor=actor, action="INTAKE_ANSWER_MANUAL",
                           resource_type=RESOURCE_TYPE, resource_id=record.id,
                           metadata={"reasonCode": reason_code}))
        result = await checked_result(session, actor, draft, record)
        await session.commit()
        return result
