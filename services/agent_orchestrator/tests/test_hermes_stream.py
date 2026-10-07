"""Only fixed fictional wire data; partial legal JSON must never become success."""
import asyncio
import json

import httpx
import pytest

from agent_orchestrator.hermes_client import (
    HermesClient,
    HermesEndpoint,
    HermesProtocolError,
    HermesUnavailable,
)


def chunk(content=None, finish=None, *, delta=None):
    return {"id": "fixture", "object": "chat.completion.chunk", "created": 0,
            "model": "hermes-agent", "choices": [{
                "index": 0, "delta": delta if delta is not None else (
                    {"content": content} if content is not None else {}
                ), "finish_reason": finish,
            }]}


def frame(payload):
    value = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return ("data: " + value + "\n\n").encode()


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, pause=False):
        self.chunks = chunks
        self.pause = pause
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        for value in self.chunks:
            yield value
        self.started.set()
        if self.pause:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


async def complete(stream, *, timeout=30, headers=None):
    async def handler(request):
        assert json.loads(request.content)["stream"] is True
        assert request.headers["accept"] == "text/event-stream"
        return httpx.Response(200, headers=headers or {"content-type": "text/event-stream"},
                              stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HermesClient(HermesEndpoint(
            "http://hermes:8642", "synthetic-only", timeout_seconds=timeout,
            stream_response=True,
        ), client=http)
        return await client.complete_json(system="fixed system", user="fixed fiction")


def test_strict_streaming_assembles_split_utf8_and_requires_done_stop():
    body = (frame(chunk(delta={"role": "assistant"})) + b": keepalive\n\n"
            + frame(chunk('{"text":"')) + frame(chunk("Вымышленный пример"))
            + frame(chunk('"}')) + frame(chunk(finish="stop")) + frame("[DONE]"))
    stream = Stream([bytes([value]) for value in body])
    assert asyncio.run(complete(stream)) == {"text": "Вымышленный пример"}
    assert stream.closed


@pytest.mark.parametrize("ending", [
    b"", frame("[DONE]"), frame(chunk(finish="length")) + frame("[DONE]"),
    frame(chunk(finish="error")) + frame("[DONE]"), frame(chunk(finish="stop")),
    frame(chunk(finish="stop")) + frame("[DONE]") + frame(chunk("extra")),
])
def test_truncated_or_failed_stream_never_accepts_even_valid_complete_json(ending):
    stream = Stream([frame(chunk('{"claims":[]}')) + ending])
    with pytest.raises(HermesProtocolError):
        asyncio.run(complete(stream))
    assert stream.closed


@pytest.mark.parametrize("wire", [
    frame('{"choices":[],"choices":[]}'),
    frame(chunk(delta={"tool_calls": [{"id": "fiction-tool"}]})),
    frame(chunk(delta={"content": {"SYNTHETIC_PRIVATE_DETAIL": "wrong-type"}})),
    b"event: hermes.tool.progress\ndata: {}\n\n", b"data: invalid\n\n", b"data: \xff\n\n",
])
def test_untrusted_stream_shapes_fail_closed_without_error_body_leak(wire):
    stream = Stream([wire])
    with pytest.raises(HermesProtocolError) as error:
        asyncio.run(complete(stream))
    assert "SYNTHETIC_PRIVATE_DETAIL" not in str(error.value)
    assert stream.closed


@pytest.mark.parametrize("content", [
    '{"claims":[],"claims":[1]}', '{"amount":NaN}', '{"amount":1e999}',
    "```json\n{}\n```", "[]",
])
def test_stream_content_retains_existing_strict_json_contract(content):
    stream = Stream([frame(chunk(content)) + frame(chunk(finish="stop")) + frame("[DONE]")])
    with pytest.raises(HermesProtocolError):
        asyncio.run(complete(stream))


def test_stream_size_cap_closes_without_consuming_remaining_bytes(monkeypatch):
    from agent_orchestrator import hermes_client
    monkeypatch.setattr(hermes_client, "MAX_ENVELOPE_BYTES", 100)
    stream = Stream([b": " + b"x" * 101 + b"\n\n", b"never-read"])
    with pytest.raises(HermesProtocolError):
        asyncio.run(complete(stream))
    assert stream.closed


def test_stream_wall_timeout_closes_connection_for_pinned_interrupt_path():
    stream = Stream([frame(chunk(delta={"role": "assistant"}))], pause=True)
    with pytest.raises(HermesUnavailable) as error:
        asyncio.run(complete(stream, timeout=1))
    assert error.value.reason.value == "WALL_TIMEOUT"
    assert stream.closed


def test_caller_cancel_closes_stream_without_becoming_provider_failure():
    async def scenario():
        stream = Stream([frame(chunk(delta={"role": "assistant"}))], pause=True)
        task = asyncio.create_task(complete(stream))
        try:
            await asyncio.wait_for(stream.started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stream.closed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_streaming_profile_refuses_silent_nonstream_response():
    stream = Stream([b'{"choices":[{"message":{"content":"{}"}}]}'])
    with pytest.raises(HermesProtocolError):
        asyncio.run(complete(stream, headers={"content-type": "application/json"}))
    assert stream.closed


def test_keepalive_trickle_does_not_reset_total_wall_deadline():
    class Trickle(Stream):
        async def __aiter__(self):
            while True:
                yield b": keepalive\n\n"
                await asyncio.sleep(0.01)

    stream = Trickle([])
    with pytest.raises(HermesUnavailable) as error:
        asyncio.run(complete(stream, timeout=1))
    assert error.value.reason.value == "WALL_TIMEOUT"
    assert stream.closed


@pytest.mark.parametrize("metadata", [
    {"completed": False}, {"failed": True}, {"partial": True},
    {"completed": "false"}, {"failed": 1}, {"partial": "true"}, [],
])
def test_explicit_failed_metadata_cannot_override_success_finish(metadata):
    final = chunk(finish="stop")
    final["hermes"] = metadata
    stream = Stream([frame(chunk('{}')) + frame(final) + frame("[DONE]")])
    with pytest.raises(HermesProtocolError):
        asyncio.run(complete(stream))
    assert stream.closed
