"""Typed HTTP client for the server-authoritative Legal Core analysis boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
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
    SemanticReviewInput,
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
    ) -> dict[str, Any]:
        headers = {"X-Telegram-User-Id": str(telegram_user_id)}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = str(idempotency_key)

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
            return AnalysisContextResponse.model_validate(payload)
        except ValidationError as exc:
            raise LegalCoreProtocolError("analysis context response contract mismatch") from exc

    async def submit_reasoning(
        self,
        *,
        context: AnalysisContextResponse,
        reasoning: ReasoningResult,
        telegram_user_id: int,
        idempotency_key: UUID,
    ) -> AnalysisSubmissionResponse:
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
        )
        payload = await self._request_json(
            "POST",
            f"/v1/cases/{context.case_id}/analysis-submissions",
            telegram_user_id=telegram_user_id,
            idempotency_key=idempotency_key,
            json_body=request.model_dump(mode="json", by_alias=True),
        )
        try:
            return AnalysisSubmissionResponse.model_validate(payload)
        except ValidationError as exc:
            raise LegalCoreProtocolError("analysis submission response contract mismatch") from exc