import os
from uuid import uuid4

import pytest
from test_case_api import actor_headers, application_client, seed_admin
from test_legal_editor_workspace_api import (
    _ingest,
    _seed_user,
    _version_payload,
    _write_manifest,
)

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


@pytest.mark.parametrize("role", ["CLINIC_OWNER", "CLINIC_LAWYER", "CLINIC_ADMIN"])
def test_library_groups_preserve_existing_role_boundary(role):
    actor = 7200000000 + uuid4().int % 100000000
    seed_admin(actor, role=role)
    with application_client() as client:
        root = client.get("/v1/legal/library/groups", headers=actor_headers(actor))
        selected = client.get("/v1/legal/library/groups/general", headers=actor_headers(actor))
        expected = 403 if role == "CLINIC_ADMIN" else 200
        assert root.status_code == selected.status_code == expected
        if expected == 200:
            assert len(root.json()["groups"]) == 7
            assert root.json()["items"] == []
            assert root.json()["selectedGroup"] is None
            assert selected.json()["selectedGroup"] == "general"
            assert not any("fragmentText" in item for item in selected.json()["items"])


def test_library_groups_reject_unknown_inactive_actor_and_invalid_navigation():
    inactive = 7300000000 + uuid4().int % 100000000
    owner = inactive + 100000001
    seed_admin(inactive, role="CLINIC_OWNER", entitlement_is_expired=True)
    seed_admin(owner, role="CLINIC_OWNER")
    with application_client() as client:
        assert (
            client.get("/v1/legal/library/groups", headers=actor_headers(inactive)).status_code
            == 403
        )
        assert (
            client.get(
                "/v1/legal/library/groups", headers=actor_headers(owner + 100000002)
            ).status_code
            == 403
        )
        assert (
            client.get("/v1/legal/library/groups/invalid", headers=actor_headers(owner)).status_code
            == 422
        )
        assert (
            client.get(
                "/v1/legal/library/groups/general?page=101", headers=actor_headers(owner)
            ).status_code
            == 422
        )


def test_grouped_library_excludes_pending_and_out_of_date_versions(tmp_path, monkeypatch):
    key = "synthetic-only-library-editor-key-0000000000"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    editor = 7400000000 + uuid4().int % 100000000
    owner = editor + 100000001
    _seed_user(editor, system_role="LEGAL_EDITOR")
    seed_admin(owner, role="CLINIC_OWNER")
    version = _ingest(
        _write_manifest(tmp_path, effective_from="2026-01-01", effective_to="2027-01-01")
    )
    path = "/v1/legal/library/groups/general"
    with application_client() as client:

        def versions(when):
            page, result = 1, []
            while True:
                response = client.get(
                    path, headers=actor_headers(owner), params={"as_of_date": when, "page": page}
                )
                assert response.status_code == 200, response.text
                body = response.json()
                result.extend(item["versionId"] for item in body["items"])
                if not body["hasMore"]:
                    return result
                page += 1

        assert str(version) not in versions("2026-10-12")
        approved = client.post(
            f"/v1/legal/review-queue/{version}/approval-events",
            headers={
                **actor_headers(editor),
                "X-Legal-Editor-Gateway-Key": key,
                "Idempotency-Key": str(uuid4()),
            },
            json=_version_payload(version),
        )
        assert approved.status_code == 200, approved.text
        assert str(version) in versions("2026-10-12")
        assert str(version) not in versions("2025-12-31")
        assert str(version) not in versions("2027-01-01")
