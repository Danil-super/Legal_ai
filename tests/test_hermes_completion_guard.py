"""Fixed fictional status fixtures only: no Hermes/provider/network/session DB."""

import asyncio
import hashlib
import json
import runpy
import sys
import types
from pathlib import Path

import httpx
import pytest
from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint, HermesProtocolError

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "ops/hermes/run_guarded_gateway.py"
SOURCE = """async def _write_sse_chat_completion(self, is_partial, is_failed, completed, err_msg,
                                     agent_error=None):
    if is_partial and err_msg and "truncat" in err_msg.lower():
        finish_reason = "length"
    elif agent_error is not None or is_failed or (not completed and err_msg):
        finish_reason = "error"
    else:
        finish_reason = "stop"
    chunk = {"finish_reason": finish_reason}
    if finish_reason != "stop":
        chunk["hermes"] = {"completed": completed, "partial": is_partial, "failed": is_failed}
    return chunk
"""


def load_guard(monkeypatch, source=SOURCE):
    namespace = runpy.run_path(str(GUARD))
    function = namespace["patched_handler_source"]
    monkeypatch.setitem(
        function.__globals__, "EXPECTED_HANDLER_SHA256", hashlib.sha256(source.encode()).hexdigest()
    )
    return function


@pytest.mark.parametrize(
    "partial,failed,completed,error,finish",
    [
        (False, False, True, None, "stop"),
        (False, False, False, None, "error"),
        (True, False, True, None, "error"),
        (True, False, False, None, "error"),
        (False, True, True, None, "error"),
        (True, False, False, "synthetic truncation", "length"),
        (True, False, True, "synthetic failure", "error"),
        (False, False, False, "synthetic failure", "error"),
    ],
)
def test_hidden_partial_or_incomplete_state_never_becomes_a_success(
    monkeypatch,
    partial,
    failed,
    completed,
    error,
    finish,
):
    transform = load_guard(monkeypatch)
    namespace = {}
    exec(compile(transform(SOURCE), "<fixed-fiction-handler>", "exec"), namespace)
    result = asyncio.run(
        namespace["_write_sse_chat_completion"](
            None,
            partial,
            failed,
            completed,
            error,
        )
    )
    assert result["finish_reason"] == finish
    if finish != "stop":
        assert result["hermes"] == {"completed": completed, "partial": partial, "failed": failed}


def test_unreviewed_source_hash_fails_without_echoing_source(monkeypatch):
    transform = load_guard(monkeypatch)
    with pytest.raises(SystemExit) as caught:
        transform(SOURCE + "# SYNTHETIC_PRIVATE_CONTENT\n")
    assert str(caught.value) == "Hermes completion-status guard mismatch; refusing to start"


@pytest.mark.parametrize("source", [SOURCE.replace("elif", "if"), SOURCE + SOURCE])
def test_exact_condition_required_even_with_matching_hash(monkeypatch, source):
    transform = load_guard(monkeypatch, source)
    with pytest.raises(SystemExit):
        transform(source)


@pytest.mark.parametrize(
    "partial,failed,completed",
    [
        (False, False, False),
        (True, False, True),
        (True, False, False),
        (False, True, True),
    ],
)
def test_guarded_hidden_failure_is_rejected_by_actual_stream_client(
    monkeypatch,
    partial,
    failed,
    completed,
):
    transform = load_guard(monkeypatch)
    namespace = {}
    exec(compile(transform(SOURCE), "<fixed-fiction-handler>", "exec"), namespace)
    result = asyncio.run(
        namespace["_write_sse_chat_completion"](
            None,
            partial,
            failed,
            completed,
            None,
        )
    )

    async def scenario():
        def frame(delta, finish=None, metadata=None):
            value = {
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": finish,
                    }
                ],
            }
            if metadata is not None:
                value["hermes"] = metadata
            return "data: " + json.dumps(value) + "\n\n"

        body = (
            frame({"content": "{}"})
            + frame({}, result["finish_reason"], result.get("hermes"))
            + "data: [DONE]\n\n"
        ).encode()
        stream = httpx.ByteStream(body)

        async def handler(request):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = HermesClient(
                HermesEndpoint(
                    "http://fiction-hermes.invalid",
                    "synthetic-key",
                    stream_response=True,
                ),
                client=http,
            )
            with pytest.raises(HermesProtocolError):
                await client.complete_json(system="fixed fiction", user="fixed fiction")

    asyncio.run(scenario())


def test_installer_changes_only_method_in_memory_and_cli_retains_it(monkeypatch):
    guard = runpy.run_path(str(GUARD))
    function = guard["install_completion_guard"]
    globals_ = function.__globals__
    monkeypatch.setitem(
        globals_, "EXPECTED_HANDLER_SHA256", hashlib.sha256(SOURCE.encode()).hexdigest()
    )

    async def original(self, *args):
        return "unpatched"

    sentinel = object()
    adapter = type(
        "APIServerAdapter", (), {"_write_sse_chat_completion": original, "unrelated": sentinel}
    )
    module = types.ModuleType("gateway.platforms.api_server")
    module.APIServerAdapter = adapter
    monkeypatch.setitem(sys.modules, "gateway.platforms.api_server", module)
    monkeypatch.setattr(globals_["inspect"], "getsource", lambda handler: SOURCE)
    calls = []

    def run_cli(path, *, run_name):
        assert adapter._write_sse_chat_completion is not original
        assert adapter.unrelated is sentinel
        calls.append((path, run_name, list(sys.argv)))

    monkeypatch.setattr(globals_["runpy"], "run_path", run_cli)
    monkeypatch.setattr(sys, "argv", [str(GUARD)])
    globals_["main"]()
    assert calls == [
        (
            "/opt/hermes/.venv/bin/hermes",
            "__main__",
            [
                "/opt/hermes/.venv/bin/hermes",
                "gateway",
                "run",
                "--no-supervise",
            ],
        )
    ]
    assert (
        asyncio.run(adapter._write_sse_chat_completion(None, False, False, False, None))[
            "finish_reason"
        ]
        == "error"
    )


def test_launcher_rejects_arguments_before_patching_or_running_cli(monkeypatch):
    guard = runpy.run_path(str(GUARD))
    globals_ = guard["main"].__globals__
    monkeypatch.setattr(sys, "argv", [str(GUARD), "unreviewed-argument"])
    monkeypatch.setitem(globals_, "install_completion_guard", lambda: pytest.fail("must not patch"))
    with pytest.raises(SystemExit, match="completion-status guard mismatch"):
        globals_["main"]()
