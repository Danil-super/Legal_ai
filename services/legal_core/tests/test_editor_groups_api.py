import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from legal_core import group_approval
from legal_core.legal_approval import LegalApprovalRejected
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
