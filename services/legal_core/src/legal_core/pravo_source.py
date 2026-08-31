"""Strict connector for the observed public publication.pravo.gov.ru JSON/PDF surface.

The portal currently exposes `/api/Documents`, `/api/Document` and `/File/Pdf`.  This module treats
that surface as an external, potentially changing input rather than a trusted schema: HTTPS, exact
host, response shape, EO number, PDF signature and size are all validated before bytes may enter the
immutable legal-update pipeline.  It never approves or promotes a legal version.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx

PRAVO_HOST = "publication.pravo.gov.ru"
PRAVO_BASE_URL = f"https://{PRAVO_HOST}"
MAX_PDF_BYTES = 50 * 1024 * 1024
_EO_NUMBER = re.compile(r"^[0-9]{16,24}$")


class PravoSourceError(RuntimeError):
    """Fail-closed error raised for transport or source-contract failures."""


@dataclass(frozen=True, slots=True)
class PravoDocumentHit:
    eo_number: str
    title: str
    publication_date: date
    document_number: str | None
    document_date: date | None
    pdf_length: int | None


@dataclass(frozen=True, slots=True)
class PravoPdfArtifact:
    eo_number: str
    source_url: str
    content: bytes
    sha256: str


class PravoPublicationClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("source timeout must be between 1 and 120 seconds")
        self._client = client
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _validate_eo_number(value: object) -> str:
        if not isinstance(value, str) or _EO_NUMBER.fullmatch(value) is None:
            raise PravoSourceError("source returned an invalid EO number")
        return value

    @staticmethod
    def _date(value: object, *, required: bool) -> date | None:
        if value in {None, ""} and not required:
            return None
        if not isinstance(value, str):
            raise PravoSourceError("source returned an invalid date field")
        raw = value.split("T", 1)[0]
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise PravoSourceError("source returned a malformed ISO date") from exc

    @staticmethod
    def _optional_positive_int(value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PravoSourceError("source returned an invalid file length")
        return value

    @staticmethod
    def _validated_url(path: str) -> str:
        url = f"{PRAVO_BASE_URL}{path}"
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != PRAVO_HOST
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise PravoSourceError("source URL escaped the trusted host")
        return url

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
    ) -> httpx.Response:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "DentalLegalAI/0.1 legal-source-watcher",
                "Accept": "application/json,application/pdf;q=0.9,*/*;q=0.1",
            },
        )
        try:
            try:
                response = await client.get(self._validated_url(path), params=params)
            except httpx.HTTPError as exc:
                raise PravoSourceError("official publication source request failed") from exc
            if response.is_redirect:
                raise PravoSourceError("official publication source redirected unexpectedly")
            if response.status_code != 200:
                raise PravoSourceError(
                    f"official publication source returned HTTP {response.status_code}"
                )
            final = response.url
            if final.scheme != "https" or final.host != PRAVO_HOST:
                raise PravoSourceError("official publication response came from an unexpected host")
            return response
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            raise PravoSourceError("official publication JSON endpoint changed content type")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PravoSourceError("official publication endpoint returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PravoSourceError("official publication JSON root must be an object")
        return payload

    def _hit(self, item: object) -> PravoDocumentHit:
        if not isinstance(item, dict):
            raise PravoSourceError("official publication list contains a non-object item")
        eo_number = self._validate_eo_number(item.get("eoNumber"))
        title_value = item.get("title") or item.get("complexName") or item.get("name")
        if not isinstance(title_value, str) or not title_value.strip():
            raise PravoSourceError("official publication item has no title")
        publication = self._date(
            item.get("publishDateShort") or item.get("publishDate"), required=True
        )
        assert publication is not None
        number = item.get("number")
        if number is not None and not isinstance(number, str):
            raise PravoSourceError("official publication item has invalid document number")
        document_date = self._date(item.get("documentDate"), required=False)
        return PravoDocumentHit(
            eo_number=eo_number,
            title=title_value.strip(),
            publication_date=publication,
            document_number=number.strip() if isinstance(number, str) and number.strip() else None,
            document_date=document_date,
            pdf_length=self._optional_positive_int(item.get("pdfFileLength")),
        )

    async def discover(
        self,
        *,
        publication_from: date,
        publication_to: date,
        page: int = 1,
        page_size: int = 100,
    ) -> tuple[PravoDocumentHit, ...]:
        if publication_to < publication_from:
            raise ValueError("publication_to must be on or after publication_from")
        if not 1 <= page <= 100_000:
            raise ValueError("page must be between 1 and 100000")
        if page_size not in {10, 30, 100, 200}:
            raise ValueError("page_size must be one of the portal-supported values")

        response = await self._request(
            "/api/Documents",
            params={
                "PublishDateFrom": publication_from.isoformat(),
                "PublishDateTo": publication_to.isoformat(),
                "PageSize": page_size,
                "Index": page,
            },
        )
        payload = self._json_object(response)
        items = payload.get("items")
        if not isinstance(items, list):
            raise PravoSourceError("official publication list has no items array")
        return tuple(self._hit(item) for item in items)

    async def document(self, eo_number: str) -> dict[str, Any]:
        validated = self._validate_eo_number(eo_number)
        response = await self._request("/api/Document", params={"eoNumber": validated})
        payload = self._json_object(response)
        returned_eo = payload.get("eoNumber")
        if returned_eo is not None and self._validate_eo_number(returned_eo) != validated:
            raise PravoSourceError("official publication document identity changed")
        return payload

    async def fetch_pdf(self, eo_number: str) -> PravoPdfArtifact:
        validated = self._validate_eo_number(eo_number)
        response = await self._request("/File/Pdf", params={"eoNumber": validated})
        content_type = response.headers.get("content-type", "").lower()
        content = response.content
        if len(content) < 5 or not content.startswith(b"%PDF-"):
            raise PravoSourceError("official publication file is not a PDF")
        if len(content) > MAX_PDF_BYTES:
            raise PravoSourceError("official publication PDF exceeds the 50 MiB safety limit")
        if content_type and "pdf" not in content_type and "octet-stream" not in content_type:
            raise PravoSourceError("official publication PDF endpoint changed content type")
        return PravoPdfArtifact(
            eo_number=validated,
            source_url=str(response.url),
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
        )
