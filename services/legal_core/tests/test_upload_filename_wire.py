"""Synthetic filename transport: original names never enter logs or audit fixtures."""

import asyncio
from uuid import UUID, uuid4

import httpx2
import pytest

from legal_core.upload_filename import decode_source_filename, source_filename_headers
from telegram_gateway.case_wizard import LegalCoreClient
from telegram_gateway.clinic_document_runtime import ClinicDocumentCoreClient


@pytest.mark.parametrize(
    "filename", ["example.txt", "пример.txt", "пример%20.txt", "акт 100%.pdf", "🦷.docx"]
)
def test_filename_wire_roundtrips_without_non_ascii_http_headers(filename: str) -> None:
    headers = source_filename_headers(filename)
    assert all(value.isascii() for value in headers.values())
    assert decode_source_filename(
        headers["X-Source-Filename"], headers.get("X-Source-Filename-Encoding")
    ) == filename


def test_legacy_ascii_percent_filename_is_not_implicitly_decoded() -> None:
    assert source_filename_headers("literal%20name.txt") == {
        "X-Source-Filename": "literal%20name.txt"
    }
    assert decode_source_filename("literal%20name.txt") == "literal%20name.txt"


@pytest.mark.parametrize(
    "filename", ["", "../a.txt", "a/b.txt", "a\\b.txt", "a\r.txt", "a\x00.txt"]
)
def test_filename_encoder_rejects_unsafe_names(filename: str) -> None:
    with pytest.raises(ValueError):
        source_filename_headers(filename)


@pytest.mark.parametrize("wire", ["a%QQ.txt", "%FF.txt", "%2Fetc.txt", "a%00.txt", "a%0D.txt"])
def test_encoded_filename_rejects_bad_encoding_and_decoded_paths(wire: str) -> None:
    with pytest.raises(ValueError):
        decode_source_filename(wire, "percent-utf8")


def test_decoded_character_limit_is_preserved_at_maximum_utf8_expansion() -> None:
    filename = "🦷" * 251 + ".txt"
    headers = source_filename_headers(filename)
    assert len(headers["X-Source-Filename"]) > 255
    assert decode_source_filename(headers["X-Source-Filename"], "percent-utf8") == filename
    with pytest.raises(ValueError):
        source_filename_headers("🦷" * 252 + ".txt")
    with pytest.raises(ValueError):
        decode_source_filename("a" * 256)


@pytest.mark.parametrize("kind", ["case", "reference", "clinic"])
def test_real_httpx_client_encodes_cyrillic_filename_before_transport(kind: str) -> None:
    async def scenario() -> None:
        requests = []

        async def capture(request):
            requests.append(request)
            return httpx2.Response(201, json={})

        async with httpx2.AsyncClient(
            base_url="http://core.example.invalid", transport=httpx2.MockTransport(capture)
        ) as http:
            client = LegalCoreClient(http)
            identifier = UUID("00000000-0000-0000-0000-000000000001")
            arguments = {
                "source_filename": "пример.txt", "content_type": "text/plain",
                "content": b"Synthetic anonymised note.",
            }
            if kind == "case":
                await client.upload_case_material(identifier, 123456, **arguments)
            elif kind == "reference":
                await client.upload_reference_evaluation_material(
                    identifier, 123456, idempotency_key=uuid4(), **arguments
                )
            else:
                await ClinicDocumentCoreClient(client=http).upload_file_version(
                    telegram_user_id=123456, document_id=identifier, valid_from=None, **arguments
                )
        assert len(requests) == 1
        request = requests[0]
        assert request.content == b"Synthetic anonymised note."
        assert decode_source_filename(
            request.headers["X-Source-Filename"], request.headers["X-Source-Filename-Encoding"]
        ) == "пример.txt"

    asyncio.run(scenario())
