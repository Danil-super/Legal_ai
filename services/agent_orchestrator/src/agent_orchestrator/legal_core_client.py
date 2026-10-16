"""Typed HTTP client for the server-authoritative Legal Core analysis boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal, overload
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import ValidationError

from agent_orchestrator.reasoning import ReasoningResult
from legal_core.analysis_contracts import (
    AnalysisClaimInput,
    AnalysisContextResponse,
    AnalysisSubmissionRequest,
    AnalysisSubmissionResponse,
    PreparationResult,
    SemanticReviewInput,
)
from legal_core.case_preparation_contracts import LimitedResult
from legal_core.intake_answer_wire import AnswerContext
from legal_core.intake_preparation_contracts import (
    IntakeUnderstandingContext,
    UnderstandingContextRequest,
)


class LegalCoreError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class LegalCoreProtocolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LegalCoreEndpoint:
    base_url: str
    internal_key: str
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Legal Core base_url must be a credential-free absolute http(s) URL")
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("Legal Core timeout must be between 1 and 120 seconds")
        if len(self.internal_key) < 32:
            raise ValueError("Legal Core internal key must contain at least 32 characters")


class LegalCoreClient:
    def __init__(
        self,
        endpoint: LegalCoreEndpoint,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self._client = client

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        telegram_user_id: int,
        idempotency_key: UUID | None = None,
        json_body: dict[str, Any] | None = None,
        job_id: UUID | None = None,
        job_token: UUID | None = None,
    ) -> dict[str, Any]:
        if (job_id is None) != (job_token is None):
            raise ValueError("analysis job id and lease token must be provided together")
        headers = {
            "X-Telegram-User-Id": str(telegram_user_id),
            "X-Agent-Internal-Key": self.endpoint.internal_key,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = str(idempotency_key)
        if job_id is not None and job_token is not None:
            headers["X-Analysis-Job-Id"] = str(job_id)
            headers["X-Analysis-Job-Token"] = str(job_token)

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.endpoint.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            try:
                response = await client.request(
                    method,
                    f"{self.endpoint.base_url.rstrip('/')}{path}",
                    headers=headers,
                    json=json_body,
                )
            except (httpx.HTTPError, TimeoutError) as exc:
                raise LegalCoreError(
                    "LEGAL_CORE_UNAVAILABLE",
                    "Legal Core request failed",
                    status_code=503,
                ) from exc

            expected = urlparse(self.endpoint.base_url)
            if response.url.scheme != expected.scheme or response.url.host != expected.hostname:
                raise LegalCoreProtocolError("Legal Core response came from an unexpected origin")

            try:
                payload = response.json()
            except ValueError as exc:
                raise LegalCoreProtocolError("Legal Core returned non-JSON data") from exc
            if not isinstance(payload, dict):
                raise LegalCoreProtocolError("Legal Core JSON response must be an object")
            if response.is_error:
                error = payload.get("error")
                if not isinstance(error, dict):
                    raise LegalCoreProtocolError("Legal Core error envelope is invalid")
                code = error.get("code")
                message = error.get("message")
                if not isinstance(code, str) or not isinstance(message, str):
                    raise LegalCoreProtocolError("Legal Core error envelope is incomplete")
                raise LegalCoreError(code, message, status_code=response.status_code)
            return payload
        finally:
            if owns_client:
                await client.aclose()

    async def get_understanding_context(
        self, *, operation_id: UUID, draft_id: UUID, telegram_user_id: int,
        binding: UnderstandingContextRequest,
    ) -> IntakeUnderstandingContext:
        failure_status = None
        try:
            request = UnderstandingContextRequest.model_validate(binding.model_dump(by_alias=True))
            payload = await self._request_json(
                "POST", f"/v1/intake-preparations/{operation_id}/context",
                telegram_user_id=telegram_user_id,
                json_body=request.model_dump(mode="json", by_alias=True),
            )
            context = IntakeUnderstandingContext.model_validate(payload)
            if (
                context.operation_id == operation_id and context.draft_id == draft_id
                and context.draft_revision == request.expected_revision
                and context.input_sha256 == request.expected_input_sha256
            ):
                return context
        except LegalCoreError as error:
            failure_status = error.status_code
        except (LegalCoreProtocolError, ValueError, TypeError):
            pass
        # Transport errors can retain the request (including keys/lease) or raw JSON body.
        # Raise outside except, dropping both explicit cause and implicit exception context.
        if failure_status is not None:
            raise LegalCoreError("UNDERSTANDING_CONTEXT_REFUSED", "Understanding context refused",
                                 status_code=failure_status)
        raise LegalCoreProtocolError("understanding context response contract mismatch")

    async def get_answer_context(
        self, *, operation_id: UUID, draft_id: UUID, telegram_user_id: int,
        binding: UnderstandingContextRequest,
    ) -> AnswerContext:
        failure_status = None
        try:
            request = UnderstandingContextRequest.model_validate(binding.model_dump(by_alias=True))
            payload = await self._request_json(
                "POST", f"/v1/intake-answer-preparations/{operation_id}/context",
                telegram_user_id=telegram_user_id,
                json_body=request.model_dump(mode="json", by_alias=True),
            )
            context = AnswerContext.model_validate(payload)
            if (
                context.operation_id == operation_id and context.draft_id == draft_id
                and context.draft_revision == request.expected_revision
                and context.input_sha256 == request.expected_input_sha256
                and context.answer_sha256 == hashlib.sha256(context.answer.encode()).hexdigest()
                and context.question_sha256 == context.question.question_sha256
                and type(context.confirmed_context.get("intakeVersion")) is int
            ):
                return context
        except LegalCoreError as error:
            failure_status = error.status_code
        except (LegalCoreProtocolError, ValueError, TypeError):
            pass
        # Neither Core errors nor malformed bodies may retain private answer/lease headers.
        if failure_status is not None:
            raise LegalCoreError("ANSWER_CONTEXT_REFUSED", "Answer context refused",
                                 status_code=failure_status)
        raise LegalCoreProtocolError("answer context response contract mismatch")

    async def get_analysis_context(
        self,
        *,
        case_id: UUID,
        telegram_user_id: int,
    ) -> AnalysisContextResponse:
        payload = await self._request_json(
            "GET",
            f"/v1/cases/{case_id}/analysis-context",
            telegram_user_id=telegram_user_id,
        )
        try:
            context = AnalysisContextResponse.model_validate(payload)
            if context.case_id != case_id:
                raise LegalCoreProtocolError("analysis context case mismatch")
            return context
        except ValidationError as exc:
            raise LegalCoreProtocolError("analysis context response contract mismatch") from exc

    async def prepare_analysis_context(
        self, *, case_id: UUID, telegram_user_id: int, idempotency_key: UUID,
        job_id: UUID | None = None, job_token: UUID | None = None,
    ) -> AnalysisContextResponse | PreparationResult:
        payload = await self._request_json(
            "POST", f"/v1/cases/{case_id}/preparation-context",
            telegram_user_id=telegram_user_id, idempotency_key=idempotency_key,
            job_id=job_id, job_token=job_token,
        )
        try:
            if "resultVersion" in payload:
                result = PreparationResult.model_validate(payload)
                if (
                    result.root.source.case_id != case_id
                    or (
                        isinstance(result.root, LimitedResult)
                        and result.root.reason_code in {
                            "ANALYSIS_DATE_UNCERTAIN", "FACTUAL_SAFETY_UNCERTAIN",
                            "LEGAL_EVIDENCE_UNAVAILABLE",
                        }
                        and result.root.source.context is not None
                    )
                ):
                    raise LegalCoreProtocolError("pre-context result source mismatch")
                # Core can replay an already verified late result (including
                # READY) for this exact intent; its real context is retained,
                # not fabricated and not sent through the models again.
                return result
            context = AnalysisContextResponse.model_validate(payload)
            if context.case_id != case_id:
                raise LegalCoreProtocolError("analysis context case mismatch")
            return context
        except ValidationError as exc:
            raise LegalCoreProtocolError("preparation context contract mismatch") from exc

    @overload
    async def submit_reasoning(
        self, *, context: AnalysisContextResponse, reasoning: ReasoningResult,
        telegram_user_id: int, idempotency_key: UUID,
        job_id: UUID | None = None, job_token: UUID | None = None,
        result_version: Literal[1] = 1,
    ) -> AnalysisSubmissionResponse: ...

    @overload
    async def submit_reasoning(
        self, *, context: AnalysisContextResponse, reasoning: ReasoningResult,
        telegram_user_id: int, idempotency_key: UUID,
        job_id: UUID | None = None, job_token: UUID | None = None,
        result_version: Literal[2],
    ) -> PreparationResult: ...

    async def submit_reasoning(
        self,
        *,
        context: AnalysisContextResponse,
        reasoning: ReasoningResult,
        telegram_user_id: int,
        idempotency_key: UUID,
        job_id: UUID | None = None,
        job_token: UUID | None = None,
        result_version: Literal[1, 2] = 1,
    ) -> AnalysisSubmissionResponse | PreparationResult:
        if type(result_version) is not int or result_version not in {1, 2}:
            raise ValueError("unsupported analysis result version")
        claims = [
            AnalysisClaimInput(
                claimId=claim.claim_id,
                kind=claim.kind.value,
                text=claim.text,
                evidenceFragmentIds=list(claim.evidence_fragment_ids),
                requiredFactKeys=list(claim.required_fact_keys),
            )
            for claim in reasoning.claims
        ]
        semantic_reviews = [
            SemanticReviewInput(
                claimId=review.claim_id,
                verdict=review.verdict.value,
                reviewedFragmentIds=list(review.reviewed_fragment_ids),
            )
            for review in reasoning.semantic_reviews
        ]
        request = AnalysisSubmissionRequest(
            asOfDate=context.as_of_date,
            expectedFactSnapshotSha256=context.fact_snapshot_sha256,
            expectedEvidenceTraceSha256=context.evidence_trace_sha256,
            expectedClinicDocumentContextTraceSha256=(
                context.clinic_document_context_trace_sha256
            ),
            expectedRiskPolicyVersion=context.risk_policy_version,
            claims=claims,
            semanticReviews=semantic_reviews,
            clarificationRequests=list(reasoning.clarification_requests),
        )
        payload = await self._request_json(
            "POST",
            f"/v1/cases/{context.case_id}/"
            + ("preparation-submissions" if result_version == 2 else "analysis-submissions"),
            telegram_user_id=telegram_user_id,
            idempotency_key=idempotency_key,
            json_body=request.model_dump(mode="json", by_alias=True),
            job_id=job_id,
            job_token=job_token,
        )
        try:
            if result_version == 2:
                result = PreparationResult.model_validate(payload)
                source = result.root.source
                bound = source.context
                if (
                    source.case_id != context.case_id
                    or source.fact_snapshot_sha256 != context.fact_snapshot_sha256
                    or bound is None or bound.as_of_date != context.as_of_date
                    or bound.evidence_trace_sha256 != context.evidence_trace_sha256
                    or bound.clinic_document_context_trace_sha256
                    != context.clinic_document_context_trace_sha256
                    or bound.risk_policy_version != context.risk_policy_version
                ):
                    raise LegalCoreProtocolError("preparation result context mismatch")
                return result
            return AnalysisSubmissionResponse.model_validate(payload)
        except ValidationError as exc:
            raise LegalCoreProtocolError("analysis submission response contract mismatch") from exc
