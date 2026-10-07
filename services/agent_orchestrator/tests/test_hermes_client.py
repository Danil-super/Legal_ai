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


@pytest.mark.parametrize(("error_type", "reason"), [
    (httpx.ConnectTimeout, "CONNECT_TIMEOUT"),
    (httpx.ReadTimeout, "READ_TIMEOUT"),
    (httpx.WriteTimeout, "WRITE_TIMEOUT"),
    (httpx.PoolTimeout, "POOL_TIMEOUT"),
    (httpx.ConnectError, "TRANSPORT"),
])
def test_unavailable_preserves_only_a_closed_transport_reason(error_type, reason) -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise error_type("SYNTHETIC_PRIVATE_PROVIDER_DETAIL", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hermes = HermesClient(
                HermesEndpoint("http://hermes:8642", "synthetic-key"), client=client,
            )
            with pytest.raises(HermesUnavailable) as caught:
                await hermes.complete_json(system="synthetic system", user="synthetic user")
            assert caught.value.reason.value == reason
            assert str(caught.value) == "Hermes API request failed"
            assert caught.value.__suppress_context__
            assert "SYNTHETIC_PRIVATE_PROVIDER_DETAIL" not in repr(caught.value)
            assert not client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [429, 503])
def test_http_status_reason_does_not_include_response_or_url(status) -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, text="SYNTHETIC_PRIVATE_PROVIDER_DETAIL")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hermes = HermesClient(
                HermesEndpoint("http://hermes:8642", "synthetic-key"), client=client,
            )
            with pytest.raises(HermesUnavailable) as caught:
                await hermes.complete_json(system="synthetic system", user="synthetic user")
            assert caught.value.reason.value == "HTTP_STATUS"
            assert str(caught.value) == "Hermes API request failed"
            assert caught.value.__suppress_context__
            assert "SYNTHETIC_PRIVATE_PROVIDER_DETAIL" not in repr(caught.value)
            assert "hermes:8642" not in repr(caught.value)

    asyncio.run(scenario())


def test_caller_cancellation_is_not_misclassified_as_provider_unavailability() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("cancelled handler must not complete")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hermes = HermesClient(
                HermesEndpoint("http://hermes:8642", "synthetic-key"), client=client,
            )
            task = asyncio.create_task(hermes.complete_json(system="system", user="synthetic"))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not client.is_closed

    asyncio.run(scenario())
