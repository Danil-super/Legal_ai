"""Bounded optional intake tasks; PostgreSQL owns the lease and unconfirmed result."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_job_worker import WorkerSettings
from legal_core.analysis_safe_stop import require_analysis_running
from legal_core.case_api import ApiError
from legal_core.intake_answer_operations import AnswerOperation
from legal_core.intake_answer_wire import AnswerProposal
from legal_core.intake_preparation import (
    PreparationOperation,
    complete_preparation,
    fail_preparation,
)
from legal_core.intake_preparation_contracts import UnderstandingInvocation, UnderstandingProposal
from legal_core.model_admission import ModelAdmission

logger = logging.getLogger(__name__)
Operation = PreparationOperation | AnswerOperation


class IntakePreparationRunner:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], settings: WorkerSettings,
        admission: ModelAdmission, *, client: httpx.AsyncClient | None = None,
    ) -> None:
        self._sessions, self._settings, self._admission = sessions, settings, admission
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=120, trust_env=False, follow_redirects=False,
            limits=httpx.Limits(max_connections=2),
        )
        self._tasks: dict[asyncio.Task[None], tuple[int, Operation]] = {}
        self._closing = False

    async def submit(self, *, telegram_user_id: int, operation: Operation) -> None:
        if operation.lease_token is None or any(
            item.operation_id == operation.operation_id for _, item in self._tasks.values()
        ):
            return  # A replay has no new claim and must not schedule another model call.
        if operation.intent is None:
            raise ValueError("new preparation claim requires server intent")
        if self._closing or not await self._admission.try_acquire():
            await self._manual(telegram_user_id, operation, "MODEL_UNAVAILABLE")
            return
        try:
            task = asyncio.create_task(self._execute(telegram_user_id, operation))
        except BaseException:
            self._admission.release()
            raise
        self._tasks[task] = (telegram_user_id, operation)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.pop(task, None)
        # A coroutine cancelled before its first instruction never enters its finally block.
        self._admission.release()

    def cancel(self, operation_id: UUID) -> None:
        for task, (_, operation) in tuple(self._tasks.items()):
            if operation.operation_id == operation_id:
                task.cancel()

    async def close(self) -> None:
        pending = dict(self._tasks)
        if not self._closing:
            self._closing = True
            for task in pending:
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for actor, operation in pending.values():
            # Also covers cancellation before the coroutine's first instruction.
            await self._manual(actor, operation, "MODEL_UNAVAILABLE")
        if self._owns_client:
            await self._client.aclose()

    async def _manual(
        self, actor: int, operation: Operation,
        code: str,
    ) -> None:
        if operation.lease_token is None:
            return
        reason: Literal["MODEL_TIMEOUT", "MODEL_PROTOCOL_INVALID", "MODEL_UNAVAILABLE"]
        reason = ("MODEL_TIMEOUT" if code == "MODEL_TIMEOUT" else
                  "MODEL_PROTOCOL_INVALID" if code == "MODEL_PROTOCOL_INVALID" else
                  "MODEL_UNAVAILABLE")
        fail: Callable[..., Awaitable[Operation]]
        try:
            if isinstance(operation, AnswerOperation):
                from legal_core.intake_answer_lifecycle import fail_answer

                fail = fail_answer
            else:
                fail = fail_preparation
            await fail(
                self._sessions, telegram_user_id=actor, operation_id=operation.operation_id,
                lease_token=operation.lease_token, reason_code=reason,
            )
        except ApiError:
            pass  # Cancellation, expiry, revocation or edit already fenced this claim.
        except Exception as error:
            logger.warning("intake_preparation_cleanup_failed operation=%s type=%s",
                           operation.operation_id, type(error).__name__)

    async def _execute(self, actor: int, operation: Operation) -> None:
        try:
            intent, token = operation.intent, operation.lease_token
            if intent is None or token is None:
                raise ValueError("preparation dispatch lacks a private claim")
            require_analysis_running()
            request = UnderstandingInvocation(
                draftId=intent.draft_id, leaseToken=token, expectedRevision=intent.draft_revision,
                expectedInputSha256=intent.input_sha256,
            )
            suffix = (f"intake-answer-preparations/{operation.operation_id}/interpret"
                      if isinstance(operation, AnswerOperation)
                      else f"intake-preparations/{operation.operation_id}/understand")
            response = await asyncio.wait_for(self._client.post(
                f"{self._settings.url}/v1/{suffix}",
                headers={"X-Agent-Internal-Key": self._settings.internal_key,
                         "X-Telegram-User-Id": str(actor)},
                json=request.model_dump(mode="json", by_alias=True),
            ), timeout=125)
            if len(response.content) > 65_536:
                raise ValueError("understanding response exceeds the bound")
            if response.is_error:
                code = response.json().get("detail", {}).get("code")
                await self._manual(actor, operation, code)
                return
            if isinstance(operation, AnswerOperation):
                await self._complete_answer(actor, operation, response)
                return
            proposal = UnderstandingProposal.model_validate(response.json())
            if (
                proposal.operation_id != operation.operation_id
                or proposal.draft_id != intent.draft_id
                or proposal.draft_revision != intent.draft_revision
                or proposal.input_sha256 != intent.input_sha256
            ):
                raise ValueError("understanding result binding mismatch")
            # Reauthorizes and validates current draft/lease after the actual network wait.
            await complete_preparation(
                self._sessions, telegram_user_id=actor, operation_id=operation.operation_id,
                lease_token=token, suggestions=proposal.suggestions,
            )
        except asyncio.CancelledError:
            await self._manual(actor, operation, "MODEL_UNAVAILABLE")
            raise
        except (TimeoutError, httpx.TimeoutException):
            await self._manual(actor, operation, "MODEL_TIMEOUT")
        except (ValueError, TypeError):
            await self._manual(actor, operation, "MODEL_PROTOCOL_INVALID")
        except Exception:
            await self._manual(actor, operation, "MODEL_UNAVAILABLE")

    async def _complete_answer(
        self, actor: int, operation: AnswerOperation, response: httpx.Response,
    ) -> None:
        from legal_core.intake_answer_lifecycle import complete_answer

        proposal = AnswerProposal.model_validate(response.json())
        intent = operation.intent
        if intent is None or operation.lease_token is None or (
            proposal.operation_id != operation.operation_id
            or proposal.draft_id != intent.draft_id
            or proposal.draft_revision != intent.draft_revision
            or proposal.input_sha256 != intent.input_sha256
            or proposal.answer_sha256 != intent.answer_sha256
            or proposal.question_sha256 != intent.question_sha256
        ):
            raise ValueError("answer result binding mismatch")
        await complete_answer(
            self._sessions, telegram_user_id=actor, operation_id=operation.operation_id,
            lease_token=operation.lease_token, interpretation=proposal.interpretation,
        )
