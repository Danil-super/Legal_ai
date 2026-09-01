"""Content-addressed MinIO storage for raw clinic document uploads."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote
from uuid import UUID

import httpx

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RawClinicDocumentStore(Protocol):
    async def put(
        self,
        *,
        clinic_id: UUID,
        document_id: UUID,
        raw_sha256: str,
        content: bytes,
        content_type: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class MinioSettings:
    host: str
    port: int
    access_key: str
    secret_key: str
    bucket: str = "clinic-documents"
    secure: bool = False
    region: str = "us-east-1"

    def __post_init__(self) -> None:
        if _HOST_RE.fullmatch(self.host) is None:
            raise ValueError("MinIO host must be a hostname or IP without a URL scheme")
        if not 1 <= self.port <= 65535:
            raise ValueError("MinIO port is invalid")
        if not self.access_key or len(self.access_key) > 128:
            raise ValueError("MinIO access key is missing or too long")
        if not self.secret_key or len(self.secret_key) > 256:
            raise ValueError("MinIO secret key is missing or too long")
        if _BUCKET_RE.fullmatch(self.bucket) is None:
            raise ValueError("MinIO bucket name is invalid")
        if not self.region or len(self.region) > 64:
            raise ValueError("MinIO region is invalid")

    @property
    def base_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def canonical_host(self) -> str:
        return f"{self.host}:{self.port}"

    @classmethod
    def from_environment(cls) -> "MinioSettings":
        access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()
        secret_key = os.getenv("MINIO_SECRET_KEY", "")
        if not access_key or not secret_key:
            raise RuntimeError("MinIO application credentials are not configured")
        secure_raw = os.getenv("MINIO_SECURE", "0").strip().casefold()
        if secure_raw not in {"0", "1", "false", "true"}:
            raise RuntimeError("MINIO_SECURE must be true/false or 1/0")
        return cls(
            host=os.getenv("MINIO_HOST", "minio").strip(),
            port=int(os.getenv("MINIO_PORT", "9000")),
            access_key=access_key,
            secret_key=secret_key,
            bucket=os.getenv("MINIO_BUCKET", "clinic-documents").strip(),
            secure=secure_raw in {"1", "true"},
            region=os.getenv("MINIO_REGION", "us-east-1").strip(),
        )


def clinic_document_object_key(
    *,
    clinic_id: UUID,
    document_id: UUID,
    raw_sha256: str,
) -> str:
    if _SHA256_RE.fullmatch(raw_sha256) is None:
        raise ValueError("raw_sha256 must be a lowercase SHA-256 hex digest")
    return f"clinic/{clinic_id}/{document_id}/{raw_sha256}"


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes:
    date_key = _hmac(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    region_key = _hmac(date_key, region)
    service_key = _hmac(region_key, "s3")
    return _hmac(service_key, "aws4_request")


class MinioRawClinicDocumentStore:
    def __init__(
        self,
        settings: MinioSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._bucket_ready = False

    def _authorization_headers(
        self,
        *,
        method: str,
        canonical_uri: str,
        payload_sha256: str,
        now: datetime,
    ) -> dict[str, str]:
        amz_date = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.astimezone(UTC).strftime("%Y%m%d")
        canonical_headers = (
            f"host:{self._settings.canonical_host}\n"
            f"x-amz-content-sha256:{payload_sha256}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            (
                method,
                canonical_uri,
                "",
                canonical_headers,
                signed_headers,
                payload_sha256,
            )
        )
        scope = f"{date_stamp}/{self._settings.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                _sha256_hex(canonical_request.encode("utf-8")),
            )
        )
        signature = hmac.new(
            _signing_key(self._settings.secret_key, date_stamp, self._settings.region),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self._settings.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {
            "Authorization": authorization,
            "Host": self._settings.canonical_host,
            "X-Amz-Content-Sha256": payload_sha256,
            "X-Amz-Date": amz_date,
        }

    async def _request(
        self,
        method: str,
        canonical_uri: str,
        *,
        content: bytes = b"",
        content_type: str | None = None,
    ) -> httpx.Response:
        headers = self._authorization_headers(
            method=method,
            canonical_uri=canonical_uri,
            payload_sha256=_sha256_hex(content),
            now=datetime.now(UTC),
        )
        if content_type is not None:
            headers["Content-Type"] = content_type
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            response = await client.request(
                method,
                f"{self._settings.base_url}{canonical_uri}",
                headers=headers,
                content=content,
            )
        except (httpx.HTTPError, TimeoutError) as exc:
            raise RuntimeError("raw clinic document storage is unavailable") from exc
        finally:
            if owns_client:
                await client.aclose()
        if 300 <= response.status_code < 400:
            raise RuntimeError("raw clinic document storage refused an unexpected redirect")
        return response

    def _bucket_uri(self) -> str:
        return "/" + quote(self._settings.bucket, safe="-_.~")

    async def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        uri = self._bucket_uri()
        head = await self._request("HEAD", uri)
        if head.status_code == 200:
            self._bucket_ready = True
            return
        if head.status_code != 404:
            raise RuntimeError(f"raw clinic document bucket check failed: HTTP {head.status_code}")
        created = await self._request("PUT", uri)
        if created.status_code not in {200, 409}:
            raise RuntimeError(
                f"raw clinic document bucket creation failed: HTTP {created.status_code}"
            )
        self._bucket_ready = True

    async def put(
        self,
        *,
        clinic_id: UUID,
        document_id: UUID,
        raw_sha256: str,
        content: bytes,
        content_type: str,
    ) -> str:
        if not content:
            raise ValueError("raw clinic document content must not be empty")
        if _sha256_hex(content) != raw_sha256:
            raise ValueError("raw clinic document SHA-256 does not match the content")
        if not content_type or len(content_type) > 200:
            raise ValueError("raw clinic document content type is invalid")
        object_key = clinic_document_object_key(
            clinic_id=clinic_id,
            document_id=document_id,
            raw_sha256=raw_sha256,
        )
        await self._ensure_bucket()
        canonical_uri = (
            self._bucket_uri() + "/" + quote(object_key, safe="/-_.~")
        )
        response = await self._request(
            "PUT",
            canonical_uri,
            content=content,
            content_type=content_type,
        )
        if response.status_code != 200:
            raise RuntimeError(f"raw clinic document upload failed: HTTP {response.status_code}")
        return object_key


def minio_store_from_environment() -> MinioRawClinicDocumentStore:
    return MinioRawClinicDocumentStore(MinioSettings.from_environment())
