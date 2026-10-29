"""Source-bound consented snippets and freshness with real PG/private storage."""

import asyncio
import hashlib
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import ApiError, resolve_actor
from legal_core.case_material_context import (
    load_case_material_context, require_case_material_context_current,
)
from legal_core.clinic_document_store import minio_store_from_environment
from legal_core.database import database_url
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1" or os.getenv("MINIO_INTEGRATION") != "1",
    reason="requires disposable PostgreSQL and private MinIO",
)
RAW = b"Synthetic anonymised material: a review appointment was offered."


def add_material(engine, clinic, case, *, allowed=True, raw=RAW, name="Материал 1"):
    material = uuid4()
    digest = hashlib.sha256(raw).hexdigest()
    store = minio_store_from_environment()
    key = asyncio.run(store.put_case_material(
        clinic_id=clinic, material_id=material, raw_sha256=digest,
        content=raw, content_type="text/plain",
    ))
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO case_materials(id,clinic_id,case_id,uploader_membership_id,"
            "display_name,mime_type,raw_size_bytes,raw_sha256,raw_object_key,expires_at,"
            "analysis_use_allowed) SELECT :id,:clinic,:case,id,:name,'text/plain',:size,"
            ":sha,:key,clock_timestamp()+interval '1 day',:allowed "
            "FROM clinic_users WHERE clinic_id=:clinic ORDER BY id LIMIT 1"
        ), {"id": material, "clinic": clinic, "case": UUID(case), "name": name,
            "size": len(raw), "sha": digest, "key": key, "allowed": allowed})
    return material, key


@pytest.fixture(autouse=True)
def enable_context(monkeypatch):
    monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "true")


def test_none_expected_cannot_accept_new_files_and_ready_report_uses_server_cards(prepared_case):
    client, _, clinic, case, headers, old_context, engine = prepared_case
    assert "caseMaterialContext" not in old_context
    material, _ = add_material(engine, clinic, case)
    path = f"/v1/cases/{case}/preparation-submissions"
    payload = _submission(old_context, questions=False, claims=True)
    rejected = client.post(path, headers=headers, json=payload)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "ANALYSIS_CONTEXT_STALE"
    current = client.get(f"/v1/cases/{case}/analysis-context", headers=headers)
    assert current.status_code == 200, current.text
    context = current.json()
    assert context["caseMaterialContext"][0]["materialId"] == str(material)
    assert context["caseMaterialContext"][0]["text"] == RAW.decode()
    payload["expectedCaseMaterialContextTraceSha256"] = context[
        "caseMaterialContextTraceSha256"
    ]
    result = client.post(path, headers=headers, json=payload)
    assert result.status_code == 201, result.text
    assert result.json()["status"] == "READY"
    canonical = result.json()["report"]["reportJson"]
    assert canonical["caseMaterialContextTraceSha256"] == context[
        "caseMaterialContextTraceSha256"
    ] == canonical["analysis"]["caseMaterialContextTraceSha256"]
    sources = canonical["caseMaterials"]["sources"]
    assert canonical["caseMaterials"]["status"] == "USED"
    assert sources[0]["materialId"] == str(material)
    assert "text" not in sources[0] and RAW.decode() not in str(sources)
    assert all(source["fragmentId"] != sources[0]["fragmentId"]
               for source in canonical["legalBasis"]["sources"])
    replay = client.post(path, headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json() == result.json()


def test_storage_only_then_consent_change_invalidates_cached_preflight_not_historical_receipt(
    prepared_case,
):
    client, _, clinic, case, headers, context, engine = prepared_case
    material, _ = add_material(engine, clinic, case, allowed=False)
    path = f"/v1/cases/{case}/preparation-submissions"
    payload = _submission(context)
    result = client.post(path, headers=headers, json=payload)
    assert result.status_code == 201, result.text
    assert result.json()["status"] == "NEEDS_FACTS"
    assert "caseMaterialContextTraceSha256" not in result.json()["source"]["context"]
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE case_materials SET analysis_use_allowed=true WHERE id=:id"
        ), {"id": material})
    retry = client.post(f"/v1/cases/{case}/preparation-context", headers=headers)
    assert retry.status_code == 409, retry.text
    assert retry.json()["error"]["code"] == "ANALYSIS_CONTEXT_STALE"
    # An exact stored receipt is a historical result, never a fresh preparation.
    historical = client.post(path, headers=headers, json=payload)
    assert historical.status_code == 200 and historical.json() == result.json()
    fresh = client.get(f"/v1/cases/{case}/analysis-context", headers=headers)
    assert fresh.status_code == 200 and fresh.json()["caseMaterialContext"]


@pytest.mark.parametrize("mutation", ["expired", "no-consent", "tampered", "wrong-key"])
def test_live_metadata_and_private_object_binding_fail_closed(prepared_case, mutation):
    client, _, clinic, case, headers, _, engine = prepared_case
    material, key = add_material(engine, clinic, case)
    if mutation == "tampered":
        # Private storage differs while immutable DB hash/size remain the old source.
        raw = b"Synthetic changed material of the same old storage intent."
        # The exact test object is overwritten directly through the signed store request.
        store = minio_store_from_environment()
        response = asyncio.run(store._request(
            "PUT", store._bucket_uri() + "/" + key,
            content=raw, content_type="text/plain",
        ))
        assert response.status_code == 200
    else:
        statements = {
            "expired": "expires_at=clock_timestamp()-interval '1 second'",
            "no-consent": "analysis_use_allowed=false",
            "wrong-key": "raw_object_key='synthetic-invalid-key'",
        }
        with engine.begin() as connection:
            connection.execute(text("UPDATE case_materials SET " + statements[mutation]
                                    + " WHERE id=:id"), {"id": material})
    response = client.get(f"/v1/cases/{case}/analysis-context", headers=headers)
    if mutation in {"expired", "no-consent"}:
        assert response.status_code == 200, response.text
        assert "caseMaterialContext" not in response.json()
    else:
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "CASE_MATERIAL_CONTEXT_UNAVAILABLE"
        assert key not in response.text and RAW.decode() not in response.text


def test_flag_off_avoids_storage_and_final_metadata_fence_rejects_new_consent(
    prepared_case, monkeypatch,
):
    _, actor, clinic, case, _, _, engine = prepared_case
    material, _ = add_material(engine, clinic, case, allowed=False)

    async def scenario():
        db = create_async_engine(database_url())
        try:
            async with async_sessionmaker(db, expire_on_commit=False)() as session:
                await resolve_actor(session, actor)
                state = await load_case_material_context(
                    session, clinic_id=clinic, case_id=UUID(case), facts={},
                )
                assert not state.fragments
                await session.execute(text(
                    "UPDATE case_materials SET analysis_use_allowed=true WHERE id=:id"
                ), {"id": material})
                with pytest.raises(ApiError) as refused:
                    await require_case_material_context_current(
                        session, clinic_id=clinic, case_id=UUID(case), state=state,
                    )
                assert refused.value.code == "ANALYSIS_CONTEXT_STALE"
                monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "false")
                disabled = await load_case_material_context(
                    session, clinic_id=clinic, case_id=UUID(case), facts={},
                )
                assert disabled.trace_sha256 is None
        finally:
            await db.dispose()

    asyncio.run(scenario())


def test_natural_expiry_after_local_parse_is_rechecked_without_reloading_raw(prepared_case):
    _, actor, clinic, case, _, _, engine = prepared_case
    material, _ = add_material(engine, clinic, case)

    async def scenario():
        db = create_async_engine(database_url())
        try:
            async with async_sessionmaker(db, expire_on_commit=False)() as session:
                await resolve_actor(session, actor)
                await session.execute(text(
                    "UPDATE case_materials SET expires_at=clock_timestamp()+interval '0.5 second' "
                    "WHERE id=:id"
                ), {"id": material})
                state = await load_case_material_context(
                    session, clinic_id=clinic, case_id=UUID(case), facts={},
                )
                assert state.fragments
                # A later FK/source/access wait can cross natural retention expiry.
                await session.execute(text("SELECT pg_sleep(0.5)"))
                with pytest.raises(ApiError) as refused:
                    await require_case_material_context_current(
                        session, clinic_id=clinic, case_id=UUID(case), state=state,
                    )
                assert refused.value.code == "ANALYSIS_CONTEXT_STALE"
        finally:
            await db.dispose()

    asyncio.run(scenario())
