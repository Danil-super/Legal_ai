import asyncio

import httpx
import pytest
from legal_core.embedding_provider import (
    EmbeddingProviderError,
    EmbeddingSettings,
    OpenAICompatibleEmbeddingProvider,
    load_embedding_settings,
)


def test_embedding_settings_are_optional_but_partial_configuration_is_rejected() -> None:
    assert load_embedding_settings({}) is None
    with pytest.raises(RuntimeError, match="partially configured"):
        load_embedding_settings(
            {
                "LEGAL_EMBEDDING_BASE_URL": "https://embeddings.example.test/v1",
                "LEGAL_EMBEDDING_MODEL": "embed-model",
            }
        )


def test_openai_compatible_provider_reorders_and_validates_vectors() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/embeddings"
            assert request.headers["authorization"] == "Bearer test-key"
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                        {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    ]
                },
                request=request,
            )

        settings = EmbeddingSettings(
            base_url="https://embeddings.example.test/v1",
            model="embed-model",
            model_key="test:embed-model:v1",
            dimensions=3,
            api_key="test-key",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            vectors = await OpenAICompatibleEmbeddingProvider(settings, client=client).embed(
                ("первый", "второй")
            )
        assert vectors == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

    asyncio.run(scenario())


def test_embedding_provider_rejects_redirect_and_wrong_dimensions() -> None:
    async def redirect_scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"location": "https://other.example.test/v1/embeddings"},
                request=request,
            )

        settings = EmbeddingSettings(
            base_url="https://embeddings.example.test/v1",
            model="embed-model",
            model_key="test:embed-model:v1",
            dimensions=3,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleEmbeddingProvider(settings, client=client)
            with pytest.raises(EmbeddingProviderError, match="redirected"):
                await provider.embed(("query",))

    async def dimension_scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
                request=request,
            )

        settings = EmbeddingSettings(
            base_url="https://embeddings.example.test/v1",
            model="embed-model",
            model_key="test:embed-model:v1",
            dimensions=3,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleEmbeddingProvider(settings, client=client)
            with pytest.raises(EmbeddingProviderError, match="vector size"):
                await provider.embed(("query",))

    asyncio.run(redirect_scenario())
    asyncio.run(dimension_scenario())
