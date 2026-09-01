from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from legal_core.clinic_document_parser import TEXT_MIME, sha256_bytes
from legal_core.clinic_document_store import minio_store_from_environment

pytestmark = pytest.mark.skipif(
    os.getenv("MINIO_INTEGRATION") != "1",
    reason="set MINIO_INTEGRATION=1 to run the real MinIO storage smoke test",
)


def test_real_minio_accepts_signed_content_addressed_upload() -> None:
    async def scenario() -> None:
        store = minio_store_from_environment()
        clinic_id = uuid4()
        document_id = uuid4()
        content = b"synthetic clinic document storage integration test"
        raw_sha256 = sha256_bytes(content)

        object_key = await store.put(
            clinic_id=clinic_id,
            document_id=document_id,
            raw_sha256=raw_sha256,
            content=content,
            content_type=TEXT_MIME,
        )

        assert object_key == f"clinic/{clinic_id}/{document_id}/{raw_sha256}"

    asyncio.run(scenario())
