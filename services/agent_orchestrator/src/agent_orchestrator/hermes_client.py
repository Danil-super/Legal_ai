"""Small authenticated client for the pinned Hermes API-server surface."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import httpx

MAX_ENVELOPE_BYTES = 1_000_000


class HermesError(RuntimeError):
    """Base class for fail-closed Hermes boundary failures."""


class HermesUnavailableReason(StrEnum):
    UNKNOWN = "UNKNOWN"
    WALL_TIMEOUT = "WALL_TIMEOUT"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    READ_TIMEOUT = "READ_TIMEOUT"
    WRITE_TIMEOUT = "WRITE_TIMEOUT"
    POOL_TIMEOUT = "POOL_TIMEOUT"
    HTTP_STATUS = "HTTP_STATUS"
    TRANSPORT = "TRANSPORT"


class HermesUnavailable(HermesError):
    def __init__(
        self,
        message: str = "Hermes API request failed",
        *,
        reason: HermesUnavailableReason = HermesUnavailableReason.UNKNOWN,
    ) -> None:
        super().__init__(message)
        self.reason = reason


def _unavailable_reason(error: httpx.HTTPError | TimeoutError) -> HermesUnavailableReason:
    # Inspect only types: provider exception messages can contain URLs, credentials or bodies.
    for error_type, reason in (
        (TimeoutError, HermesUnavailableReason.WALL_TIMEOUT),
        (httpx.ConnectTimeout, HermesUnavailableReason.CONNECT_TIMEOUT),
        (httpx.ReadTimeout, HermesUnavailableReason.READ_TIMEOUT),
        (httpx.WriteTimeout, HermesUnavailableReason.WRITE_TIMEOUT),
        (httpx.PoolTimeout, HermesUnavailableReason.POOL_TIMEOUT),
        (httpx.HTTPStatusError, HermesUnavailableReason.HTTP_STATUS),
    ):
        if isinstance(error, error_type):
            return reason
    return HermesUnavailableReason.TRANSPORT


class HermesProtocolError(HermesError):
    pass


@dataclass(frozen=True, slots=True)
class HermesEndpoint:
    base_url: str
    api_key: str
    model: str = "hermes-agent"
    timeout_seconds: float = 30.0
    stream_response: bool = False

    def __post_init__(self) -> None:
        try:
            parsed = urlparse(self.base_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and (parsed.port is None or 1 <= parsed.port <= 65535)
                and not any(character.isspace() for character in self.base_url)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Hermes base_url must be a credential-free absolute http(s) URL")
        if not self.api_key:
            raise ValueError("Hermes API key must not be empty")
        if not self.model or len(self.model) > 120:
            raise ValueError("Hermes model/profile name must be between 1 and 120 characters")
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("Hermes timeout must be between 1 and 120 seconds")
        if type(self.stream_response) is not bool:
            raise ValueError("Hermes stream_response must be a boolean")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HermesProtocolError("Hermes JSON contains duplicate object keys")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    del value
    raise HermesProtocolError("Hermes JSON contains a non-standard numeric constant")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise HermesProtocolError("Hermes JSON number exceeds the supported range")
    return number


def _decode_json(value: str | bytes) -> Any:
    try:
        return json.loads(
            value, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (ValueError, RecursionError):
        # Never attach a decoder exception containing the model's response body.
        raise HermesProtocolError("Hermes response is not valid JSON") from None


def _require_not_failed(payload: dict[str, Any]) -> None:
    metadata = payload.get("hermes")
    if "hermes" in payload and (not isinstance(metadata, dict) or any(
        key in metadata and type(metadata[key]) is not bool
        for key in ("completed", "failed", "partial")
    )):
        raise HermesProtocolError("Hermes response has invalid completion metadata")
    if "error" in payload or (isinstance(metadata, dict) and (
        metadata.get("completed") is False or metadata.get("failed") is True
        or metadata.get("partial") is True
    )):
        raise HermesProtocolError("Hermes response did not complete successfully")


async def _read_sse_content(response: httpx.Response) -> str:
    """Consume only the pinned tool-free chat stream, bounded before decoding.

    Closing this stream on timeout/caller cancellation reaches the pinned
    gateway's disconnect interrupt path; it does not instantly kill a worker.
    A syntactically valid partial JSON is never a completed response.
    """
    if response.headers.get("content-type", "").split(";", 1)[0].lower() != "text/event-stream":
        raise HermesProtocolError("Hermes streaming response must use event-stream encoding")
    pending = bytearray()
    search_start = total_bytes = content_size = 0
    data: str | None = None
    stopped = done = False
    parts: list[str] = []
    async for raw in response.aiter_bytes():
        total_bytes += len(raw)
        if total_bytes > MAX_ENVELOPE_BYTES:
            raise HermesProtocolError("Hermes response exceeded the envelope limit")
        pending.extend(raw)
        while True:
            newline = pending.find(b"\n", search_start)
            if newline < 0:
                search_start = len(pending)
                break
            raw_line = bytes(pending[:newline]).removesuffix(b"\r")
            del pending[:newline + 1]
            search_start = 0
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                raise HermesProtocolError("Hermes stream is not valid UTF-8") from None
            if line.startswith(":"):
                continue
            if line:
                if not line.startswith("data:") or data is not None or done:
                    raise HermesProtocolError("Hermes stream has an unsupported event")
                data = line[5:].removeprefix(" ")
                continue
            if data is None:
                continue
            frame, data = data, None
            if frame == "[DONE]":
                if not stopped or done:
                    raise HermesProtocolError("Hermes stream did not complete successfully")
                done = True
                continue
            payload = _decode_json(frame)
            if not isinstance(payload, dict) or payload.get("object") != "chat.completion.chunk":
                raise HermesProtocolError("Hermes stream has an invalid completion chunk")
            _require_not_failed(payload)
            choices = payload.get("choices")
            if (not isinstance(choices, list) or len(choices) != 1
                    or not isinstance(choices[0], dict)):
                raise HermesProtocolError("Hermes stream has an invalid completion chunk")
            choice = choices[0]
            if type(choice.get("index")) is not int or choice["index"] != 0:
                raise HermesProtocolError("Hermes stream has an invalid completion chunk")
            finish = choice.get("finish_reason")
            if stopped or finish not in (None, "stop"):
                raise HermesProtocolError("Hermes stream did not complete successfully")
            delta = choice.get("delta")
            if not isinstance(delta, dict) or set(delta) - {"role", "content"}:
                raise HermesProtocolError("Hermes stream has an unsupported delta")
            if "role" in delta and delta["role"] != "assistant":
                raise HermesProtocolError("Hermes stream has an unsupported delta")
            if "content" in delta:
                if not isinstance(delta["content"], str):
                    raise HermesProtocolError("Hermes stream has a non-text delta")
                content_size += len(delta["content"])
                if content_size > 80_000:
                    raise HermesProtocolError("Hermes response exceeded the bounded response limit")
                parts.append(delta["content"])
            if finish == "stop":
                stopped = True
    if pending or data is not None or not done:
        raise HermesProtocolError("Hermes stream did not complete successfully")
    return "".join(parts)


class HermesClient:
    """Call one least-privilege Hermes profile and require a strict JSON response body."""

    def __init__(
        self,
        endpoint: HermesEndpoint,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self._client = client

    async def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        if not system.strip() or not user.strip():
            raise ValueError("Hermes prompts must not be blank")
        if len(system) > 20_000 or len(user) > 120_000:
            raise ValueError("Hermes prompt exceeded the bounded context limit")

        request = {
            "model": self.endpoint.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": self.endpoint.stream_response,
        }
        headers = {
            "Authorization": f"Bearer {self.endpoint.api_key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }
        if self.endpoint.stream_response:
            headers["Accept"] = "text/event-stream"
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.endpoint.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            content: str | None = None
            try:
                # Bound total wall time as well as individual HTTP operations. Streaming the
                # envelope avoids buffering an unlimited body before checking message.content.
                async with asyncio.timeout(self.endpoint.timeout_seconds), client.stream(
                    "POST", f"{self.endpoint.base_url.rstrip('/')}/v1/chat/completions",
                    headers=headers, json=request, follow_redirects=False,
                    timeout=self.endpoint.timeout_seconds,
                ) as response:
                    response.raise_for_status()
                    expected = httpx.URL(self.endpoint.base_url)
                    actual = response.url
                    if (actual.scheme, actual.host, actual.port) != (
                        expected.scheme, expected.host, expected.port,
                    ):
                        raise HermesProtocolError("Hermes response came from an unexpected origin")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise HermesProtocolError("Hermes response must use identity encoding")
                    declared = response.headers.get("content-length")
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except ValueError:
                            message = "Hermes response has invalid length"
                            raise HermesProtocolError(message) from None
                        if not 0 <= declared_size <= MAX_ENVELOPE_BYTES:
                            raise HermesProtocolError("Hermes response exceeded the envelope limit")
                    body = bytearray()
                    if self.endpoint.stream_response:
                        content = await _read_sse_content(response)
                    else:
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > MAX_ENVELOPE_BYTES:
                                raise HermesProtocolError(
                                    "Hermes response exceeded the envelope limit"
                                )
                            body.extend(chunk)
            except (httpx.HTTPError, TimeoutError) as exc:
                raise HermesUnavailable(reason=_unavailable_reason(exc)) from None

            if not self.endpoint.stream_response:
                payload = _decode_json(bytes(body))
                try:
                    choice = payload["choices"][0]
                    content = choice["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    raise HermesProtocolError(
                        "Hermes returned an invalid chat-completion envelope"
                    ) from None
                _require_not_failed(payload)
                # Legacy nonstream callers may omit finish_reason. An explicit
                # incomplete/failed finish can never override syntactically valid JSON.
                if "finish_reason" in choice and choice["finish_reason"] != "stop":
                    raise HermesProtocolError("Hermes response did not complete successfully")
            if not isinstance(content, str) or not content.strip():
                raise HermesProtocolError("Hermes returned an empty/non-text response")
            if len(content) > 80_000:
                raise HermesProtocolError("Hermes response exceeded the bounded response limit")

            stripped = content.strip()
            if stripped.startswith("```"):
                message = "Hermes response must be raw JSON without markdown fences"
                raise HermesProtocolError(message)
            decoded = _decode_json(stripped)
            if not isinstance(decoded, dict):
                raise HermesProtocolError("Hermes JSON response must be an object")
            return decoded
        finally:
            if owns_client:
                await client.aclose()
