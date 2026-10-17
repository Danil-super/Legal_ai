"""Durable unconfirmed answers; transactions finish before optional model I/O."""

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_safe_stop import require_analysis_running
from legal_core.case_api import ActorContext, ApiError, _audit, _canonical_hash, resolve_actor
from legal_core.case_preparation_contracts import PreparationModel
from legal_core.intake_answer_contracts import (
    AnswerInterpretation,
    AnswerQuestion,
    derive_answer_question,
)
from legal_core.intake_answer_intent import AnswerPreparationIntent
from legal_core.intake_answer_values import validate_answer_shape, validate_answer_value
from legal_core.intake_answer_wire import AnswerStatusResponse
from legal_core.intake_preparation import (
    LEASE_DURATION,
    RESOURCE_TYPE,
    _draft,
    _locked_owned_operation,
    _recheck_after_locks,
)
from legal_core.intake_preparation_contracts import (
    AcceptUnderstandingRequest,
    UnderstandingAcceptance,
    UnderstandingSource,
)
from legal_core.models import AuditEvent, IdempotencyRecord, TelegramIntakeDraft
from legal_core.pseudonymization import pseudonymize_text


@dataclass(frozen=True)
class AnswerOperation:
    response: AnswerStatusResponse
    lease_token: UUID | None = None
    intent: AnswerPreparationIntent | None = None

    @property
    def operation_id(self) -> UUID:
        return self.response.operation_id


class StoredAnswer(PreparationModel):
    intent: AnswerPreparationIntent
    question: AnswerQuestion
    answer: str = Field(strict=True, min_length=1, max_length=1500)
    interpretation: AnswerInterpretation | None = None
    acceptance: dict[str, Any] | None = Field(default=None, max_length=6)


def proposal_metadata(stored: StoredAnswer) -> dict[str, Any]:
    if stored.interpretation is None:
        raise ApiError(status_code=409, code="ANSWER_RESULT_INVALID",
                       message="Answer result is not available")
    intent = stored.intent
    return {
        "draftId": str(intent.draft_id), "revision": intent.draft_revision,
        "inputSha256": intent.input_sha256, "intentSha256": intent.request_sha256,
        "questionSha256": intent.question_sha256, "answerSha256": intent.answer_sha256,
        "proposalSha256": _canonical_hash(stored.interpretation.model_dump(
            mode="json", by_alias=True,
        )),
    }


async def _audit_matches(
    session: AsyncSession, actor: ActorContext, record: IdempotencyRecord,
    action: str, metadata: dict[str, Any],
) -> bool:
    audits = list((await session.scalars(select(AuditEvent.metadata_json).where(
        AuditEvent.clinic_id == actor.clinic_id,
        AuditEvent.actor_membership_id == actor.membership_id,
        AuditEvent.resource_type == RESOURCE_TYPE, AuditEvent.resource_id == record.id,
        AuditEvent.action == action,
    ).limit(2))).all())
    return audits == [metadata]


async def checked_proposal(
    session: AsyncSession, actor: ActorContext, record: IdempotencyRecord,
    stored: StoredAnswer, draft: TelegramIntakeDraft | None = None,
) -> dict[str, Any]:
    valid = False
    try:
        interpretation = stored.interpretation
        if interpretation is not None:
            interpretation.validate_source(stored.answer)
            if interpretation.status == "PROPOSED":
                if draft is None:
                    validate_answer_shape(stored.question, interpretation.value,
                                          as_of_date=stored.intent.as_of_date)
                else:
                    validate_answer_value(stored.question, interpretation.value, stored.answer,
                                          draft.draft_json, as_of_date=stored.intent.as_of_date)
                if stored.question.target_field == "affectedServices" and (
                    not isinstance(interpretation.value, list) or any(
                        not isinstance(item, str) or not any(
                            item in stored.answer[span.start:span.end]
                            for span in interpretation.source_spans
                        ) for item in interpretation.value
                    )
                ):
                    raise ValueError("service is not grounded in an answer anchor")
            valid = (record.state == "SUCCEEDED" and record.lease_token is None
                     and record.lease_until is None)
    except (ValueError, TypeError):
        valid = False
    if not valid or not await _audit_matches(
        session, actor, record, "INTAKE_ANSWER_PROPOSED", proposal_metadata(stored),
    ):
        raise ApiError(status_code=409, code="ANSWER_PROPOSAL_INVALID",
                       message="Answer proposal binding is not valid")
    return proposal_metadata(stored)


async def acceptance_receipt(
    session: AsyncSession, actor: ActorContext, draft: TelegramIntakeDraft,
    record: IdempotencyRecord, stored: StoredAnswer,
) -> dict[str, Any] | None:
    receipt = stored.acceptance
    if receipt is None:
        return None
    metadata = await checked_proposal(session, actor, record, stored)
    valid = False
    try:
        request = AcceptUnderstandingRequest(
            expectedRevision=stored.intent.draft_revision,
            expectedInputSha256=stored.intent.input_sha256,
            expectedProposalSha256=metadata["proposalSha256"],
        )
        valid = (
            set(receipt) == {"acceptedRevision", "requestSha256", "proposalSha256", "outputSha256"}
            and type(receipt["acceptedRevision"]) is int
            and receipt["acceptedRevision"] == stored.intent.draft_revision + 1
            and draft.revision >= receipt["acceptedRevision"]
            and stored.interpretation is not None and stored.interpretation.status == "PROPOSED"
            and receipt["proposalSha256"] == metadata["proposalSha256"]
            and receipt["requestSha256"] == _canonical_hash(request.model_dump(
                mode="json", by_alias=True,
            ))
            and isinstance(receipt["outputSha256"], str)
            and len(receipt["outputSha256"]) == 64
            and all(char in "0123456789abcdef" for char in receipt["outputSha256"])
            and (draft.revision != receipt["acceptedRevision"]
                 or input_hash(draft) == receipt["outputSha256"])
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid or not await _audit_matches(
        session, actor, record, "INTAKE_ANSWER_ACCEPTED", {**metadata, **receipt},
    ):
        raise ApiError(status_code=409, code="ANSWER_ACCEPTANCE_INVALID",
                       message="Answer acceptance binding is not valid")
    return receipt


async def checked_result(
    session: AsyncSession, actor: ActorContext, draft: TelegramIntakeDraft,
    record: IdempotencyRecord,
) -> AnswerOperation:
    historical = stored_answer(actor, draft, record, fresh=False)
    receipt = await acceptance_receipt(session, actor, draft, record, historical)
    if receipt is not None:
        return AnswerOperation(AnswerStatusResponse(
            operationId=record.id, state="ACCEPTED", acceptance=UnderstandingAcceptance(
                draftId=draft.id, acceptedRevision=receipt["acceptedRevision"],
            ),
        ))
    bound = stored_answer(actor, draft, record)
    if record.state != "SUCCEEDED":
        return pending_result(record)
    metadata = await checked_proposal(session, actor, record, bound, draft)
    assert bound.interpretation is not None  # checked_proposal established the closed result.
    return AnswerOperation(AnswerStatusResponse(
        operationId=record.id, state=bound.interpretation.status, question=bound.question,
        interpretation=bound.interpretation, source=UnderstandingSource(
            draftId=draft.id, draftRevision=draft.revision,
            inputSha256=bound.intent.input_sha256, proposalSha256=metadata["proposalSha256"],
        ),
    ))


def checked_source(answer: str) -> str:
    if (not isinstance(answer, str) or not 1 <= len(answer) <= 1500 or not answer.strip()
            or pseudonymize_text(answer).changed):
        raise ApiError(status_code=422, code="ANSWER_INPUT_INVALID",
                       message="A bounded de-identified factual answer is required")
    return answer


def current_question(draft: TelegramIntakeDraft) -> AnswerQuestion:
    try:
        question = derive_answer_question(draft.draft_json, draft.wizard_state)
    except (KeyError, TypeError, ValueError):
        question = None
    if question is None:
        raise ApiError(status_code=409, code="ANSWER_QUESTION_UNAVAILABLE",
                       message="There is no current factual question")
    return question


def input_hash(draft: TelegramIntakeDraft) -> str:
    return _canonical_hash({"draftData": draft.draft_json, "wizardState": draft.wizard_state})


def stored_answer(
    actor: ActorContext, draft: TelegramIntakeDraft, record: IdempotencyRecord,
    *, fresh: bool = True,
) -> StoredAnswer:
    checked = None
    with suppress(KeyError, TypeError, ValueError):
        checked = StoredAnswer.model_validate(record.response_json)
    if checked is None:
        raise ApiError(status_code=409, code="ANSWER_INTENT_INVALID",
                       message="Answer operation binding is not valid")
    intent = checked.intent
    checked_source(checked.answer)
    if (intent.clinic_id != actor.clinic_id or intent.actor_membership_id != actor.membership_id
            or intent.draft_id != draft.id or record.resource_id != draft.id
            or record.resource_type != RESOURCE_TYPE or record.scope != intent.scope
            or record.key != str(intent.idempotency_key)
            or record.request_sha256 != intent.request_sha256
            or intent.answer_sha256 != _canonical_hash({"answer": checked.answer})
            or intent.question_sha256 != checked.question.question_sha256):
        raise ApiError(status_code=409, code="ANSWER_INTENT_INVALID",
                       message="Answer operation binding is not valid")
    if fresh and (intent.draft_revision != draft.revision
                  or intent.input_sha256 != input_hash(draft)
                  or checked.question != current_question(draft)):
        raise ApiError(status_code=409, code="ANSWER_INPUT_STALE",
                       message="The factual question or saved draft has changed")
    return checked


def pending_result(record: IdempotencyRecord) -> AnswerOperation:
    if record.state == "IN_PROGRESS":
        if record.lease_until is not None and record.lease_until > datetime.now(UTC):
            return AnswerOperation(AnswerStatusResponse(operationId=record.id, state="PENDING"))
        record.state, record.lease_token, record.lease_until = "FAILED", None, None
    if record.state != "FAILED":
        # Completed answers require their separate immutable proposal/consent decoder.
        raise ApiError(status_code=409, code="ANSWER_RESULT_INVALID",
                       message="Answer result is not available")
    return AnswerOperation(AnswerStatusResponse(operationId=record.id, state="MANUAL_CONTINUATION"))


async def live_records(
    session: AsyncSession, actor: ActorContext, draft: TelegramIntakeDraft,
    question: AnswerQuestion,
    *, answer_sha256: str | None = None,
) -> list[IdempotencyRecord]:
    statement = select(IdempotencyRecord).where(
        IdempotencyRecord.clinic_id == actor.clinic_id,
        IdempotencyRecord.actor_membership_id == actor.membership_id,
        IdempotencyRecord.resource_id == draft.id, IdempotencyRecord.resource_type == RESOURCE_TYPE,
        IdempotencyRecord.scope == f"draft-answer:v1:{draft.id}",
        IdempotencyRecord.response_json["intent"]["draftRevision"].as_string()
        == str(draft.revision),
        IdempotencyRecord.response_json["intent"]["inputSha256"].as_string() == input_hash(draft),
        IdempotencyRecord.response_json["intent"]["questionSha256"].as_string()
        == question.question_sha256,
    )
    if answer_sha256 is not None:
        # Exact retries retain the original reference date, including cancelled attempts.
        statement = statement.where(
            IdempotencyRecord.response_json["intent"]["answerSha256"].as_string() == answer_sha256,
        )
    else:
        statement = statement.where(or_(IdempotencyRecord.state == "SUCCEEDED", and_(
            IdempotencyRecord.state == "IN_PROGRESS",
            IdempotencyRecord.lease_until > func.clock_timestamp(),
        )))
    statement = statement.limit(2).with_for_update().execution_options(populate_existing=True)
    return list((await session.scalars(statement)).all())


async def reserve_answer(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int,
    draft_id: UUID, expected_revision: int, answer: str,
) -> AnswerOperation:
    require_analysis_running()
    checked_source(answer)
    async with sessions() as session:
        actor = await resolve_actor(session, telegram_user_id)
        draft = await _draft(session, actor, draft_id)
        if type(expected_revision) is not int or expected_revision != draft.revision:
            raise ApiError(status_code=409, code="INTAKE_DRAFT_REVISION_CONFLICT",
                           message="Draft revision has changed")
        question = current_question(draft)
        answer_hash = _canonical_hash({"answer": answer})
        exact = await live_records(session, actor, draft, question, answer_sha256=answer_hash)
        await _recheck_after_locks(session, telegram_user_id, actor, draft)
        if len(exact) > 1:
            raise ApiError(status_code=409, code="ANSWER_OPERATION_AMBIGUOUS",
                           message="The current answer operation is ambiguous")
        if exact:
            stored_answer(actor, draft, exact[0])
            result = await checked_result(session, actor, draft, exact[0])
            await session.commit()
            return result
        intent = AnswerPreparationIntent(
            clinicId=actor.clinic_id, actorMembershipId=actor.membership_id,
            draftId=draft.id, draftRevision=draft.revision, inputSha256=input_hash(draft),
            answerSha256=answer_hash,
            questionSha256=question.question_sha256, asOfDate=datetime.now(UTC).date(),
        )
        rows = await live_records(session, actor, draft, question)
        if any(row.key != str(intent.idempotency_key) for row in rows) or len(rows) > 1:
            await _recheck_after_locks(session, telegram_user_id, actor, draft)
            raise ApiError(status_code=409, code="ANSWER_OPERATION_PENDING",
                           message="Cancel the current answer before replacing it")
        record_id, token = uuid4(), uuid4()
        inserted = await session.scalar(insert(IdempotencyRecord).values(
            id=record_id, clinic_id=actor.clinic_id, actor_membership_id=actor.membership_id,
            scope=intent.scope, key=str(intent.idempotency_key),
            request_sha256=intent.request_sha256,
            resource_type=RESOURCE_TYPE, resource_id=draft.id, state="IN_PROGRESS",
            lease_token=token, lease_until=datetime.now(UTC) + LEASE_DURATION,
            response_json=StoredAnswer(intent=intent, question=question, answer=answer).model_dump(
                mode="json", by_alias=True,
            ),
        ).on_conflict_do_nothing(index_elements=[
            "scope", "actor_membership_id", "key",
        ]).returning(IdempotencyRecord.id))
        record = await session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.scope == intent.scope,
            IdempotencyRecord.actor_membership_id == actor.membership_id,
            IdempotencyRecord.clinic_id == actor.clinic_id,
            IdempotencyRecord.key == str(intent.idempotency_key),
        ).with_for_update().execution_options(populate_existing=True))
        await _recheck_after_locks(session, telegram_user_id, actor, draft)
        if record is None:
            raise ApiError(status_code=409, code="ANSWER_INTENT_INVALID",
                           message="Answer operation binding is not valid")
        stored_answer(actor, draft, record)
        result = await checked_result(session, actor, draft, record)
        if inserted is not None:
            result = AnswerOperation(result.response, lease_token=token, intent=intent)
            session.add(_audit(actor=actor, action="INTAKE_ANSWER_RESERVED",
                               resource_type=RESOURCE_TYPE, resource_id=record.id,
                               metadata={"revision": draft.revision,
                                         "questionSha256": question.question_sha256}))
        await session.commit()
        return result


async def discover_answer(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, draft_id: UUID,
) -> AnswerOperation | None:
    async with sessions() as session:
        actor = await resolve_actor(session, telegram_user_id)
        draft = await _draft(session, actor, draft_id)
        if draft.wizard_state in {"MATERIALS", "SUMMARY", "V2_CONFIRM"}:
            # Navigation fences also run here, but these are uploads/consent, not answers.
            await _recheck_after_locks(session, telegram_user_id, actor, draft)
            return None
        question = current_question(draft)
        rows = await live_records(session, actor, draft, question)
        await _recheck_after_locks(session, telegram_user_id, actor, draft)
        if len(rows) > 1:
            raise ApiError(status_code=409, code="ANSWER_OPERATION_AMBIGUOUS",
                           message="The current answer operation is ambiguous")
        if not rows:
            return None
        stored_answer(actor, draft, rows[0])
        result = await checked_result(session, actor, draft, rows[0])
        await session.commit()
        return result


async def read_answer(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, operation_id: UUID,
) -> AnswerOperation:
    async with sessions() as session:
        actor, draft, record = await _locked_owned_operation(
            session, telegram_user_id, operation_id,
        )
        result = await checked_result(session, actor, draft, record)
        await session.commit()
        return result


async def cancel_answer(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, operation_id: UUID,
) -> AnswerOperation:
    async with sessions() as session:
        actor, draft, record = await _locked_owned_operation(
            session, telegram_user_id, operation_id,
        )
        historical = stored_answer(actor, draft, record, fresh=False)
        if await acceptance_receipt(session, actor, draft, record, historical) is not None:
            raise ApiError(status_code=409, code="ANSWER_ALREADY_ACCEPTED",
                           message="Confirmed facts cannot be cancelled")
        stored_answer(actor, draft, record)
        if record.state != "FAILED":
            record.state, record.lease_token, record.lease_until = "FAILED", None, None
            session.add(_audit(actor=actor, action="INTAKE_ANSWER_CANCELLED",
                               resource_type=RESOURCE_TYPE, resource_id=record.id, metadata={}))
        result = pending_result(record)
        await session.commit()
        return result
