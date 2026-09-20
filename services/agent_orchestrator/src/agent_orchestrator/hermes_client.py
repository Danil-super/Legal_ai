"""Small authenticated client for the pinned Hermes API-server surface."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

MAX_ENVELOPE_BYTES = 1_000_000


class HermesError(RuntimeError):
    """Base class for fail-closed Hermes boundary failures."""


class HermesUnavailable(HermesError):
    pass


class HermesProtocolError(HermesError):
    pass


@dataclass(frozen=True, slots=True)
class HermesEndpoint:
    base_url: str
    api_key: str
    model: str = "hermes-agent"
    timeout_seconds: float = 30.0

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
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.endpoint.api_key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.endpoint.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        try:
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
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_ENVELOPE_BYTES:
                            raise HermesProtocolError("Hermes response exceeded the envelope limit")
                        body.extend(chunk)
            except (httpx.HTTPError, TimeoutError):
                raise HermesUnavailable("Hermes API request failed") from None

            payload = _decode_json(bytes(body))
            try:
                content = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise HermesProtocolError("Hermes returned an invalid chat-completion envelope") \
                    from None
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
