"""Optional OpenAI-compatible embedding boundary for public legal text and safe legal queries.

Embedding is a retrieval enhancement, never a source of legal truth. Legal Core still filters by
APPROVED versions and effective dates and the verifier still gates every legal claim. The provider
is optional: when it is unavailable, retrieval safely falls back to local lexical search.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

_MODEL_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,119}$")
MAX_EMBEDDING_INPUTS = 32
MAX_EMBEDDING_TEXT_CHARS = 12_000
MAX_EMBEDDING_TOTAL_CHARS = 80_000


class EmbeddingProviderError(RuntimeError):
    """A bounded external embedding request could not be trusted or completed."""


class EmbeddingProvider(Protocol):
    model_key: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    base_url: str
    model: str
    model_key: str
    dimensions: int
    api_key: str | None = None
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "embedding base URL must be an absolute http(s) URL without credentials"
            )
        if not 1 <= len(self.model) <= 200:
            raise ValueError("embedding model must be between 1 and 200 characters")
        if _MODEL_KEY.fullmatch(self.model_key) is None:
            raise ValueError("embedding model key must be a stable, versioned identifier")
        if not 1 <= self.dimensions <= 4096:
            raise ValueError("embedding dimensions must be between 1 and 4096")
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("embedding timeout must be between 1 and 120 seconds")

    @property
    def endpoint_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/embeddings"


def load_embedding_settings(
    environment: Mapping[str, str] | None = None,
) -> EmbeddingSettings | None:
    source = os.environ if environment is None else environment
    base_url = source.get("LEGAL_EMBEDDING_BASE_URL", "").strip()
    model = source.get("LEGAL_EMBEDDING_MODEL", "").strip()
    model_key = source.get("LEGAL_EMBEDDING_MODEL_KEY", "").strip()
    dimensions_raw = source.get("LEGAL_EMBEDDING_DIMENSIONS", "").strip()
    api_key = source.get("LEGAL_EMBEDDING_API_KEY", "").strip() or None

    required = (base_url, model, model_key, dimensions_raw)
    if not any(required):
        return None
    if not all(required):
        raise RuntimeError(
            "embedding retrieval is partially configured; base URL, model, model key and "
            "dimensions are all required"
        )
    try:
        dimensions = int(dimensions_raw)
    except ValueError as exc:
        raise RuntimeError("LEGAL_EMBEDDING_DIMENSIONS must be an integer") from exc
    return EmbeddingSettings(
        base_url=base_url,
        model=model,
        model_key=model_key,
        dimensions=dimensions,
        api_key=api_key,
    )


def embedding_provider_from_environment(
    environment: Mapping[str, str] | None = None,
) -> EmbeddingProvider | None:
    settings = load_embedding_settings(environment)
    return None if settings is None else OpenAICompatibleEmbeddingProvider(settings)


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("URL has no host")
    return parsed.scheme, hostname, parsed.port


class OpenAICompatibleEmbeddingProvider:
    """Strict `/v1/embeddings`-style client with no redirect or response-shape guessing."""

    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.model_key = settings.model_key
        self.dimensions = settings.dimensions
        self._client = client
        self._expected_origin = _origin(settings.base_url)

    def _validate_inputs(self, texts: Sequence[str]) -> tuple[str, ...]:
        if not 1 <= len(texts) <= MAX_EMBEDDING_INPUTS:
            raise ValueError(f"embedding batch must contain 1..{MAX_EMBEDDING_INPUTS} texts")
        normalized: list[str] = []
        total = 0
        for text in texts:
            value = text.strip()
            if not value or len(value) > MAX_EMBEDDING_TEXT_CHARS:
                raise ValueError("embedding text is blank or exceeds the per-item limit")
            total += len(value)
            normalized.append(value)
        if total > MAX_EMBEDDING_TOTAL_CHARS:
            raise ValueError("embedding batch exceeds the total character limit")
        return tuple(normalized)

    def _parse_embedding(self, raw: object) -> tuple[float, ...]:
        if not isinstance(raw, list) or len(raw) != self.dimensions:
            raise EmbeddingProviderError("embedding provider returned an unexpected vector size")
        values: list[float] = []
        for item in raw:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise EmbeddingProviderError("embedding provider returned a non-numeric vector")
            value = float(item)
            if not math.isfinite(value) or abs(value) > 1_000_000:
                raise EmbeddingProviderError("embedding provider returned an invalid numeric value")
            values.append(value)
        return tuple(values)

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        normalized = self._validate_inputs(texts)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.settings.api_key is not None:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            try:
                response = await client.post(
                    self.settings.endpoint_url,
                    headers=headers,
                    json={"model": self.settings.model, "input": list(normalized)},
                )
            except httpx.HTTPError as exc:
                raise EmbeddingProviderError("embedding provider request failed") from exc
            if response.is_redirect:
                raise EmbeddingProviderError("embedding provider redirected unexpectedly")
            if response.status_code != 200:
                raise EmbeddingProviderError(
                    f"embedding provider returned HTTP {response.status_code}"
                )
            if _origin(str(response.url)) != self._expected_origin:
                raise EmbeddingProviderError("embedding provider response changed origin")
            if "json" not in response.headers.get("content-type", "").lower():
                raise EmbeddingProviderError("embedding provider changed response content type")
            try:
                payload = response.json()
            except ValueError as exc:
                raise EmbeddingProviderError("embedding provider returned invalid JSON") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise EmbeddingProviderError("embedding provider response envelope is invalid")

            indexed: dict[int, tuple[float, ...]] = {}
            for item in payload["data"]:
                if not isinstance(item, dict):
                    raise EmbeddingProviderError("embedding provider data item is invalid")
                index = item.get("index")
                if isinstance(index, bool) or not isinstance(index, int):
                    raise EmbeddingProviderError("embedding provider data index is invalid")
                if index in indexed or not 0 <= index < len(normalized):
                    raise EmbeddingProviderError(
                        "embedding provider returned an invalid data index"
                    )
                indexed[index] = self._parse_embedding(item.get("embedding"))
            if set(indexed) != set(range(len(normalized))):
                raise EmbeddingProviderError("embedding provider omitted one or more vectors")
            return tuple(indexed[index] for index in range(len(normalized)))
        finally:
            if owns_client:
                await client.aclose()
