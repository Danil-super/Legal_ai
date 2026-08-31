import asyncio
from datetime import date

import httpx
import pytest
from legal_core.pravo_source import PravoPublicationClient, PravoSourceError


EO_NUMBER = "0001202606010083"


def test_discover_validates_and_maps_publication_items() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "publication.pravo.gov.ru"
            assert request.url.path == "/api/Documents"
            assert request.url.params["PublishDateFrom"] == "2026-08-31"
            return httpx.Response(
                200,
                headers={"content-type": "application/json; charset=utf-8"},
                json={
                    "items": [
                        {
                            "eoNumber": EO_NUMBER,
                            "title": "Постановление Правительства Российской Федерации",
                            "publishDateShort": "2026-06-01T00:00:00",
                            "number": "659",
                            "documentDate": "2026-05-30T00:00:00",
                            "pdfFileLength": 4_162_290,
                        }
                    ]
                },
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = PravoPublicationClient(client=client)
            hits = await source.discover(
                publication_from=date(2026, 8, 31),
                publication_to=date(2026, 8, 31),
            )

        assert len(hits) == 1
        assert hits[0].eo_number == EO_NUMBER
        assert hits[0].publication_date == date(2026, 6, 1)
        assert hits[0].document_number == "659"
        assert hits[0].pdf_length == 4_162_290

    asyncio.run(scenario())


def test_source_rejects_redirects_instead_of_following_them() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"location": "https://example.com/evil"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = PravoPublicationClient(client=client)
            with pytest.raises(PravoSourceError, match="redirected"):
                await source.discover(
                    publication_from=date(2026, 8, 31),
                    publication_to=date(2026, 8, 31),
                )

    asyncio.run(scenario())


def test_source_rejects_malformed_list_contract() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"items": [{"eoNumber": "bad", "title": "Act"}]},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = PravoPublicationClient(client=client)
            with pytest.raises(PravoSourceError, match="EO number"):
                await source.discover(
                    publication_from=date(2026, 8, 31),
                    publication_to=date(2026, 8, 31),
                )

    asyncio.run(scenario())


def test_fetch_pdf_preserves_bytes_and_sha256() -> None:
    async def scenario() -> None:
        pdf = b"%PDF-1.7\nsynthetic official artifact"

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/File/Pdf"
            assert request.url.params["eoNumber"] == EO_NUMBER
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=pdf,
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            artifact = await PravoPublicationClient(client=client).fetch_pdf(EO_NUMBER)

        assert artifact.content == pdf
        assert artifact.sha256 == "80eaec90d66b90248d98f8423e1e85370eca1c038d177e01bbe357127b51d90a"
        assert artifact.source_url.startswith("https://publication.pravo.gov.ru/File/Pdf")

    asyncio.run(scenario())


def test_fetch_pdf_rejects_non_pdf_response() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html>maintenance</html>",
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = PravoPublicationClient(client=client)
            with pytest.raises(PravoSourceError, match="not a PDF"):
                await source.fetch_pdf(EO_NUMBER)

    asyncio.run(scenario())
