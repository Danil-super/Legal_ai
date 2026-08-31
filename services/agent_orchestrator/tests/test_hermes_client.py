import asyncio

import httpx
import pytest
from agent_orchestrator.hermes_client import (
    HermesClient,
    HermesEndpoint,
    HermesProtocolError,
    HermesUnavailable,
)


def test_hermes_client_parses_strict_json_response() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer secret"
            assert request.url.path == "/v1/chat/completions"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": '{"ok": true}'}}
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hermes = HermesClient(
                HermesEndpoint(base_url="http://hermes:8642", api_key="secret"),
                client=client,
            )
            assert await hermes.complete_json(system="system", user="user") == {"ok": True}

    asyncio.run(scenario())


def test_hermes_client_rejects_markdown_fenced_json() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "```json\n{}\n```"}}
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hermes = HermesClient(
                HermesEndpoint(base_url="http://hermes:8642", api_key="secret"),
                client=client,
            )
            with pytest.raises(HermesProtocolError, match="raw JSON"):
                await hermes.complete_json(system="system", user="user")

    asyncio.run(scenario())


def test_hermes_client_fails_closed_on_http_error() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(503, json={"error": "unavailable"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hermes = HermesClient(
                HermesEndpoint(base_url="http://hermes:8642", api_key="secret"),
                client=client,
            )
            with pytest.raises(HermesUnavailable):
                await hermes.complete_json(system="system", user="user")

    asyncio.run(scenario())
