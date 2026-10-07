import asyncio
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from legal_core import group_approval
from legal_core.legal_approval import LegalApprovalRejected
from legal_core.material_preparation import store_preparation
from legal_core.models import LegalReviewMaterial, User
from legal_core.normative_preparation import bind_prepared_part
from legal_core.database import database_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_prepared_material_versions import _bundle_input, _raw, _write_manifest as _part_manifest
from legal_core.corpus_loader import ingest_manifest
from test_legal_editor_workspace_api import _client, _ingest, _seed_user, _write_manifest
from test_legal_editor_workspace_api import _approval_event_count
from test_review_material_api import _seed_material

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


def test_groups_combine_materials_and_versions_without_disclosing_raw_text(tmp_path, monkeypatch):
    key = "group-api-test-key-00000000000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actor = 9_100_000_000 + uuid4().int % 100_000_000
    _seed_user(actor, system_role="LEGAL_EDITOR")
    material_id, _ = _seed_material("Проверочный общий документ")
    version_id = _ingest(_write_manifest(tmp_path))
    headers = {"X-Telegram-User-Id": str(actor), "X-Legal-Editor-Gateway-Key": key}
    with _client() as client:
        assert client.get("/v1/legal/editor/groups").status_code in (403, 422)
        root = client.get("/v1/legal/editor/groups", headers=headers)
        assert root.status_code == 200
        assert len(root.json()["groups"]) == 7
        assert root.json()["items"] == []
        group = client.get("/v1/legal/editor/groups/general", headers=headers)
        assert group.status_code == 200
        items = group.json()["items"]
        for page in range(2, (group.json()["totalItems"] + 9) // 10 + 1):
            items.extend(
                client.get(f"/v1/legal/editor/groups/general?page={page}", headers=headers).json()[
                    "items"
                ]
            )
        assert any(item["materialId"] == str(material_id) for item in items)
        assert any(item["versionId"] == str(version_id) for item in items)
        assert all("rawBytes" not in item and "normalizedText" not in item for item in items)
        assert client.get("/v1/legal/editor/groups/unknown", headers=headers).status_code == 422


def test_matching_raw_checksum_without_part_binding_does_not_hide_original(tmp_path, monkeypatch):
    key = "group-api-test-key-00000000000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actor = 9_110_000_000 + uuid4().int % 100_000_000
    _seed_user(actor, system_role="LEGAL_EDITOR")
    version_id = _ingest(_write_manifest(tmp_path))
    raw = b"%PDF-1.7\nlegal-editor-workspace-api-test\n%%EOF\n"

    async def seed_original():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                material = LegalReviewMaterial(
                    package_key=f"p10-same-bytes-{uuid4().hex}", original_filename="synthetic.pdf",
                    title="Synthetic general source", kind="LEGAL_COPY", source_name="Synthetic",
                    raw_mime_type="application/pdf", raw_bytes=raw,
                    raw_size_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest(),
                    received_at=datetime.now(UTC),
                )
                session.add(material)
                await session.flush()
                return material.id
        finally:
            await engine.dispose()

    material_id = asyncio.run(seed_original())
    headers = {"X-Telegram-User-Id": str(actor), "X-Legal-Editor-Gateway-Key": key}
    with _client() as client:
        first = client.get("/v1/legal/editor/groups/general", headers=headers).json()
        items = first["items"]
        for page in range(2, (first["totalItems"] + 9) // 10 + 1):
            items.extend(client.get(
                f"/v1/legal/editor/groups/general?page={page}", headers=headers
            ).json()["items"])
        assert any(item["materialId"] == str(material_id) and item["versionId"] is None
                   for item in items)
        assert any(item["versionId"] == str(version_id) and item["materialId"] is None
                   for item in items)
        assert first["progress"]["unpreparedOriginals"] >= 1
        assert first["progress"]["unlinkedVersions"] >= 1
        assert first["progress"]["complete"] is False


def test_group_approval_is_explicit_atomic_and_retry_safe(tmp_path, monkeypatch):
    key = "group-api-test-key-00000000000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actor = 9_200_000_000 + uuid4().int % 100_000_000
    _seed_user(actor, system_role="LEGAL_EDITOR")
    version_id = _ingest(_write_manifest(tmp_path))
    headers = {"X-Telegram-User-Id": str(actor), "X-Legal-Editor-Gateway-Key": key}
    with _client() as client:
        preview = client.get("/v1/legal/editor/groups/general/approval-preview", headers=headers)
        assert preview.status_code == 200
        body = preview.json()
        assert str(version_id) in [item["versionId"] for item in body["ready"]]
        assert body["blocked"]
        request = {
            "expectedSnapshot": body["snapshot"],
            "versionIds": [item["versionId"] for item in body["ready"]],
            "officialTextCompared": True,
            "artifactIsComplete": True,
            "effectiveDatesVerified": True,
            "fragmentsVerified": True,
        }
        endpoint = "/v1/legal/editor/groups/general/approval-events"
        confirm_headers = {**headers, "Idempotency-Key": str(uuid4())}
        assert (
            client.post(
                endpoint,
                headers=confirm_headers,
                json={
                    **request,
                    "fragmentsVerified": False,
                },
            ).status_code
            == 422
        )
        approved = client.post(endpoint, headers=confirm_headers, json=request)
        assert approved.status_code == 200
        assert approved.json()["approvedCount"] == len(request["versionIds"])
        retry = client.post(endpoint, headers=confirm_headers, json=request)
        assert retry.json() == approved.json()
        assert _approval_event_count(version_id) == 1
        assert (
            client.post(
                endpoint,
                headers=confirm_headers,
                json={
                    **request,
                    "expectedSnapshot": "0" * 64,
                },
            ).status_code
            == 422
        )


def test_group_approval_rejects_stale_membership_without_approving(tmp_path, monkeypatch):
    key = "group-api-test-key-00000000000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actor = 9_300_000_000 + uuid4().int % 100_000_000
    _seed_user(actor, system_role="LEGAL_EDITOR")
    version_id = _ingest(_write_manifest(tmp_path))
    headers = {"X-Telegram-User-Id": str(actor), "X-Legal-Editor-Gateway-Key": key}
    with _client() as client:
        body = client.get(
            "/v1/legal/editor/groups/general/approval-preview", headers=headers
        ).json()
        _seed_material("Позже добавленный общий материал")
        response = client.post(
            "/v1/legal/editor/groups/general/approval-events",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "expectedSnapshot": body["snapshot"],
                "versionIds": [item["versionId"] for item in body["ready"]],
                "officialTextCompared": True,
                "artifactIsComplete": True,
                "effectiveDatesVerified": True,
                "fragmentsVerified": True,
            },
        )
        assert response.status_code == 409
        assert _approval_event_count(version_id) == 0


def _group_request(body):
    return {
        "expectedSnapshot": body["snapshot"],
        "versionIds": [item["versionId"] for item in body["ready"]],
        "officialTextCompared": True,
        "artifactIsComplete": True,
        "effectiveDatesVerified": True,
        "fragmentsVerified": True,
    }


def test_group_approval_rolls_back_every_event_when_a_later_version_fails(tmp_path, monkeypatch):
    key = "group-api-test-key-00000000000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actor = 9_400_000_000 + uuid4().int % 100_000_000
    _seed_user(actor, system_role="LEGAL_EDITOR")
    for _ in range(2):
        _ingest(_write_manifest(tmp_path))
    headers = {
        "X-Telegram-User-Id": str(actor),
        "X-Legal-Editor-Gateway-Key": key,
        "Idempotency-Key": str(uuid4()),
    }
    original = group_approval.approve_legal_version_in_session
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise LegalApprovalRejected("SYNTHETIC_FAILURE")
        return await original(*args, **kwargs)

    monkeypatch.setattr(group_approval, "approve_legal_version_in_session", fail_second)
    with _client() as client:
        body = client.get(
            "/v1/legal/editor/groups/general/approval-preview", headers=headers
        ).json()
        response = client.post(
            "/v1/legal/editor/groups/general/approval-events",
            headers=headers,
            json=_group_request(body),
        )
        assert response.status_code == 409
    assert calls == 2
    assert all(_approval_event_count(UUID(item["versionId"])) == 0 for item in body["ready"])


@pytest.mark.parametrize("same_intent", [True, False])
def test_concurrent_group_confirmations_do_not_duplicate_decisions(
    tmp_path, monkeypatch, same_intent
):
    key = "group-api-test-key-00000000000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actors = [9_500_000_000 + uuid4().int % 100_000_000 for _ in range(2)]
    for actor in actors:
        _seed_user(actor, system_role="LEGAL_EDITOR")
    version = _ingest(_write_manifest(tmp_path))
    headers = {
        "X-Telegram-User-Id": str(actors[0]),
        "X-Legal-Editor-Gateway-Key": key,
        "Idempotency-Key": str(uuid4()),
    }
    second_headers = (
        headers
        if same_intent
        else {
            **headers,
            "X-Telegram-User-Id": str(actors[1]),
            "Idempotency-Key": str(uuid4()),
        }
    )
    with _client() as client:
        body = client.get(
            "/v1/legal/editor/groups/general/approval-preview", headers=headers
        ).json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda h: client.post(
                        "/v1/legal/editor/groups/general/approval-events",
                        headers=h,
                        json=_group_request(body),
                    ),
                    [headers, second_headers],
                )
            )
        assert sorted(response.status_code for response in responses) == (
            [200, 200] if same_intent else [200, 409]
        )
    assert _approval_event_count(version) == 1


def test_group_endpoints_do_not_grant_editor_access_to_ordinary_user(monkeypatch):
    key = "group-api-test-key-00000000000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actor = 9_600_000_000 + uuid4().int % 100_000_000
    _seed_user(actor)
    headers = {
        "X-Telegram-User-Id": str(actor),
        "X-Legal-Editor-Gateway-Key": key,
        "Idempotency-Key": str(uuid4()),
    }
    with _client() as client:
        for path in ("", "/general", "/general/approval-preview"):
            assert client.get("/v1/legal/editor/groups" + path, headers=headers).status_code == 403
        body = {"snapshot": "a" * 64, "ready": [{"versionId": str(uuid4())}]}
        assert (
            client.post(
                "/v1/legal/editor/groups/general/approval-events",
                headers=headers,
                json=_group_request(body),
            ).status_code
            == 403
        )


def test_group_endpoints_require_gateway_key_even_for_editor(monkeypatch):
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", "group-api-test-key-00000000000000001")
    actor = 9_700_000_000 + uuid4().int % 100_000_000
    _seed_user(actor, system_role="LEGAL_EDITOR")
    headers = {"X-Telegram-User-Id": str(actor), "Idempotency-Key": str(uuid4())}
    with _client() as client:
        for path in ("", "/general", "/general/approval-preview"):
            assert client.get("/v1/legal/editor/groups" + path, headers=headers).status_code == 403
        assert client.post(
            "/v1/legal/editor/groups/general/approval-events", headers=headers,
            json=_group_request({"snapshot": "a" * 64, "ready": [{"versionId": str(uuid4())}]}),
        ).status_code == 403


def test_clinical_group_cannot_approve_a_legal_version_from_another_group(tmp_path, monkeypatch):
    key = "group-api-test-key-00000000000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actor = 9_800_000_000 + uuid4().int % 100_000_000
    _seed_user(actor, system_role="LEGAL_EDITOR")
    version = _ingest(_write_manifest(tmp_path))
    headers = {"X-Telegram-User-Id": str(actor), "X-Legal-Editor-Gateway-Key": key,
               "Idempotency-Key": str(uuid4())}
    with _client() as client:
        preview = client.get(
            "/v1/legal/editor/groups/clinical/approval-preview", headers=headers
        ).json()
        assert preview["ready"] == []
        request = {**_group_request(preview), "versionIds": [str(version)]}
        assert client.post(
            "/v1/legal/editor/groups/clinical/approval-events", headers=headers, json=request
        ).status_code == 409
    assert _approval_event_count(version) == 0


def test_partially_bound_bundle_keeps_original_visible_and_blocks_group_completion(
    tmp_path, monkeypatch
):
    key = "group-api-test-key-00000000000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actor = 9_900_000_000 + uuid4().int % 100_000_000
    prepared = _bundle_input()

    async def setup():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            first = await ingest_manifest(factory, _part_manifest(tmp_path, prepared, 0))
            second = await ingest_manifest(factory, _part_manifest(tmp_path, prepared, 1))
            async with factory() as session, session.begin():
                raw = _raw(prepared.title)
                material = LegalReviewMaterial(
                    package_key=f"p10-bundle-{uuid4().hex}", original_filename="bundle.rtf",
                    title=prepared.title, kind="LEGAL_COPY", source_name="Synthetic source",
                    raw_mime_type="application/rtf", raw_bytes=raw,
                    raw_size_bytes=len(raw), raw_sha256=prepared.raw_sha256,
                    received_at=datetime.now(UTC),
                )
                editor = User(telegram_user_id=actor, status="ACTIVE", system_role="LEGAL_EDITOR")
                session.add_all([material, editor])
                await session.flush()
                preparation = await store_preparation(session, material.id, prepared)
                material_id, preparation_id, actor_id = material.id, preparation.id, editor.id
            async with factory() as session, session.begin():
                await bind_prepared_part(
                    session, preparation_id=preparation_id, part_key="part-1",
                    legal_version_id=first, actor_user_id=actor_id,
                )
            return material_id, preparation_id, actor_id, first, second
        finally:
            await engine.dispose()

    material_id, preparation_id, actor_id, first, second = asyncio.run(setup())
    headers = {"X-Telegram-User-Id": str(actor), "X-Legal-Editor-Gateway-Key": key}
    with _client() as client:
        page = client.get("/v1/legal/editor/groups/general", headers=headers)
        assert page.status_code == 200
        body = page.json()
        items = body["items"]
        for number in range(2, (body["totalItems"] + 9) // 10 + 1):
            items.extend(client.get(
                f"/v1/legal/editor/groups/general?page={number}", headers=headers
            ).json()["items"])
        original = next(item for item in items if item["materialId"] == str(material_id)
                        and item["versionId"] is None)
        linked = next(item for item in items if item["versionId"] == str(first))
        assert original["expectedParts"] == 2
        assert original["linkedParts"] == 1
        assert linked["materialId"] == str(material_id)
        assert linked["partKey"] == "part-1"
        assert body["progress"]["missingParts"] >= 1
        assert body["progress"]["complete"] is False
        detail = client.get(
            f"/v1/legal/review-materials/{material_id}/preparation", headers=headers
        ).json()
        assert {part["part_key"] for part in detail["parts"]} == {"part-1", "part-2"}
        assert detail["linkedPartKeys"] == ["part-1"]
        preview = client.get(
            "/v1/legal/editor/groups/general/approval-preview", headers=headers
        ).json()
        assert any(item["title"] == prepared.title and item["reasonCode"] == "PARTS_UNBOUND"
                   for item in preview["blocked"])

    async def bind_second():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                await bind_prepared_part(
                    session, preparation_id=preparation_id, part_key="part-2",
                    legal_version_id=second, actor_user_id=actor_id,
                )
        finally:
            await engine.dispose()

    asyncio.run(bind_second())
    with _client() as client:
        body = client.get("/v1/legal/editor/groups/general", headers=headers).json()
        renewed_preview = client.get(
            "/v1/legal/editor/groups/general/approval-preview", headers=headers
        ).json()
        assert renewed_preview["snapshot"] != preview["snapshot"]
        items = body["items"]
        for number in range(2, (body["totalItems"] + 9) // 10 + 1):
            items.extend(client.get(
                f"/v1/legal/editor/groups/general?page={number}", headers=headers
            ).json()["items"])
        assert not any(item["materialId"] == str(material_id) and item["versionId"] is None
                       for item in items)
        assert {item["versionId"] for item in items if item["materialId"] == str(material_id)} == {
            str(first), str(second)
        }
        assert client.get(
            f"/v1/legal/review-materials/{material_id}/preparation", headers=headers
        ).json()["linkedPartKeys"] == ["part-1", "part-2"]
        assert client.get(
            f"/v1/legal/review-materials/{material_id}/artifact", headers=headers
        ).status_code == 200
