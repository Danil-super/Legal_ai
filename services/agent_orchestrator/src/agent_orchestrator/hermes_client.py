"""Small authenticated client for the pinned Hermes API-server surface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


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
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Hermes base_url must be a credential-free absolute http(s) URL")
        if not self.api_key:
            raise ValueError("Hermes API key must not be empty")
        if not self.model or len(self.model) > 120:
            raise ValueError("Hermes model/profile name must be between 1 and 120 characters")
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("Hermes timeout must be between 1 and 120 seconds")


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
        }
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.endpoint.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            try:
                response = await client.post(
                    f"{self.endpoint.base_url.rstrip('/')}/v1/chat/completions",
                    headers=headers,
                    json=request,
                )
                response.raise_for_status()
            except (httpx.HTTPError, TimeoutError) as exc:
                raise HermesUnavailable("Hermes API request failed") from exc

            expected = urlparse(self.endpoint.base_url)
            final = response.url
            if final.scheme != expected.scheme or final.host != expected.hostname:
                raise HermesProtocolError("Hermes response came from an unexpected origin")

            try:
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                message = "Hermes returned an invalid chat-completion envelope"
                raise HermesProtocolError(message) from exc
            if not isinstance(content, str) or not content.strip():
                raise HermesProtocolError("Hermes returned an empty/non-text response")
            if len(content) > 80_000:
                raise HermesProtocolError("Hermes response exceeded the bounded response limit")

            stripped = content.strip()
            if stripped.startswith("```"):
                message = "Hermes response must be raw JSON without markdown fences"
                raise HermesProtocolError(message)
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise HermesProtocolError("Hermes response is not valid JSON") from exc
            if not isinstance(decoded, dict):
                raise HermesProtocolError("Hermes JSON response must be an object")
            return decoded
        finally:
            if owns_client:
                await client.aclose()
