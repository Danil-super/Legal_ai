"""Preparation source order is assigned at storage, not at transaction start."""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.analysis_contracts import PreparationResult
from legal_core.case_api import resolve_actor
from legal_core.case_preparation import store_preparation_result
from legal_core.database import database_url
from legal_core.models import Case, IdempotencyRecord
from test_preparation_precontext import frozen_precontext as frozen_precontext
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


def test_receipt_order_uses_current_storage_clock_even_in_an_older_transaction(frozen_precontext):
    client, telegram_actor, case_id, headers, _ = frozen_precontext
    response = client.post(f"/v1/cases/{case_id}/preparation-context", headers=headers)
    assert response.status_code == 201, response.text
    wire = response.json()
    wire["source"]["operationId"] = str(uuid4())
    result = PreparationResult.model_validate(wire)
    key = uuid4()

    async def store():
        engine = create_async_engine(database_url())
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                actor = await resolve_actor(session, telegram_actor)
                case = await session.scalar(select(Case).where(
                    Case.id == UUID(case_id)
                ).with_for_update())
                # Actual SQL wait separates transaction now() from storage time.
                await session.execute(select(func.pg_sleep(0.01)))
                before_storage = await session.scalar(select(func.clock_timestamp()))
                await store_preparation_result(
                    session, actor=actor, case=case,
                    scope=f"cases:{case_id}:preparation-submissions", idempotency_key=key,
                    request_hash="a" * 64, result=result,
                )
                await session.commit()
                await resolve_actor(session, telegram_actor)
                record = await session.scalar(select(IdempotencyRecord).where(
                    IdempotencyRecord.id == result.root.source.operation_id,
                ))
                assert record.created_at >= before_storage
        finally:
            await engine.dispose()

    asyncio.run(store())
