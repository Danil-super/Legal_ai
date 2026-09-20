from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_orchestrator import hermes_client as module
from agent_orchestrator.hermes_client import (
    HermesClient,
    HermesEndpoint,
    HermesProtocolError,
    HermesUnavailable,
)


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], delay: float = 0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def envelope(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


async def complete(response: httpx.Response, *, redirects: bool = False):
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=redirects,
    ) as client:
        hermes = HermesClient(
            HermesEndpoint("http://hermes:8642", "synthetic-key", timeout_seconds=1), client=client,
        )
        try:
            return await hermes.complete_json(system="system", user="synthetic user")
        finally:
            assert len(seen) == 1
            assert seen[0].headers["accept-encoding"] == "identity"
            assert not client.is_closed


@pytest.mark.parametrize("content", [
    '{"claims":[],"claims":[1]}', '{"value":NaN}', '{"value":Infinity}',
    '{"value":-Infinity}', '{"nested":{"ok":1,"ok":2}}',
    '{"value":1e999}', '{"value":-1e999}',
])
def test_non_strict_model_json_is_rejected(content: str) -> None:
    with pytest.raises(HermesProtocolError):
        asyncio.run(complete(httpx.Response(200, content=envelope(content))))


def test_duplicate_envelope_keys_are_rejected() -> None:
    response = httpx.Response(200, content=b'{"choices": [], "choices": []}')
    with pytest.raises(HermesProtocolError, match="duplicate"):
        asyncio.run(complete(response))


@pytest.mark.parametrize("declared", [None, "1", "99999999", "-1", "invalid"])
def test_envelope_bound_does_not_trust_content_length(declared, monkeypatch) -> None:
    monkeypatch.setattr(module, "MAX_ENVELOPE_BYTES", 100)
    stream = ResponseStream([b"x" * 60, b"x" * 60, b"never read"])
    headers = {} if declared is None else {"content-length": declared}
    response = httpx.Response(200, stream=stream, headers=headers)
    with pytest.raises(HermesProtocolError):
        asyncio.run(complete(response))
    assert stream.closed
    assert stream.consumed <= 2


def test_total_timeout_closes_a_slow_response_stream() -> None:
    stream = ResponseStream([b" "] * 10, delay=0.3)
    with pytest.raises(HermesUnavailable):
        asyncio.run(complete(httpx.Response(200, stream=stream)))
    assert stream.closed
    assert stream.consumed < 10


def test_redirect_is_not_followed_even_with_a_permissive_injected_client() -> None:
    response = httpx.Response(307, headers={"location": "http://other.invalid/collect"})
    with pytest.raises(HermesUnavailable):
        asyncio.run(complete(response, redirects=True))


def test_compressed_response_is_rejected_before_reading() -> None:
    stream = ResponseStream([b"not even a valid gzip body"])
    response = httpx.Response(200, stream=stream, headers={"content-encoding": "gzip"})
    with pytest.raises(HermesProtocolError, match="identity"):
        asyncio.run(complete(response))
    assert stream.consumed == 0
    assert stream.closed


@pytest.mark.parametrize("url", [
    "http://hermes:bad", "http://hermes:0", "http://hermes:65536", "http://[invalid",
    "http://user:synthetic@hermes", "http://hermes/ space", "file:///tmp/test",
])
def test_invalid_endpoint_is_rejected_early(url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        HermesEndpoint(url, "synthetic-key")


def test_truncated_json_is_a_sanitized_protocol_error() -> None:
    with pytest.raises(HermesProtocolError) as error:
        asyncio.run(complete(httpx.Response(200, content=envelope('{"SYNTHETIC_PRIVATE_TEXT":'))))
    assert "SYNTHETIC_PRIVATE_TEXT" not in str(error.value)
    assert error.value.__suppress_context__


def test_deeply_nested_json_fails_as_protocol_error() -> None:
    with pytest.raises(HermesProtocolError):
        asyncio.run(complete(httpx.Response(200, content=envelope('[' * 2000 + ']' * 2000))))


def test_regular_response_still_works() -> None:
    assert asyncio.run(complete(httpx.Response(200, content=envelope('{"claims":[]}')))) == {
        "claims": [],
    }
