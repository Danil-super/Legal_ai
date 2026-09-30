"""Synthetic end-to-end tests for the isolated anonymised material boundary."""

import os
from uuid import UUID, uuid4

import pytest

from legal_core.clinic_document_store import case_material_object_key
from test_case_api import actor_headers, application_client, seed_admin, workflow_submission

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


class FakeCaseMaterialStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []

    async def put_case_material(
        self,
        *,
        clinic_id: UUID,
        material_id: UUID,
        raw_sha256: str,
        content: bytes,
        content_type: str,
    ) -> str:
        key = case_material_object_key(
            clinic_id=clinic_id, material_id=material_id, raw_sha256=raw_sha256
        )
        self.objects[key] = (content, content_type)
        return key

    async def get_case_material(self, *, object_key: str, max_bytes: int) -> bytes:
        content, _ = self.objects[object_key]
        assert len(content) <= max_bytes
        return content

    async def delete_case_material(self, *, object_key: str) -> None:
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)


def _new_draft(client: object, actor: int, *, version: int = 2) -> dict[str, object]:
    response = client.post(  # type: ignore[attr-defined]
        "/v1/telegram-intake-drafts",
        json={"intakeSchemaVersion": f"dental-case-intake.v{version}"},
        headers=actor_headers(actor, uuid4()),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload_headers(actor: int, key: UUID, filename: str = "synthetic-note.txt") -> dict[str, str]:
    return {
        **actor_headers(actor, key),
        "X-Source-Filename": filename,
        "Content-Type": "text/plain",
    }


def test_owner_can_upload_replay_list_download_and_delete_without_filename_leakage() -> None:
    actor = 8_400_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    store = FakeCaseMaterialStore()
    raw = b"Synthetic anonymised message: a clinic appointment needs review."
    with application_client(case_material_store=store) as client:
        draft = _new_draft(client, actor)
        draft_id = draft["id"]
        replay_key = uuid4()
        created = client.post(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
            content=raw,
            headers=_upload_headers(actor, replay_key),
        )
        replay = client.post(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
            content=raw,
            headers=_upload_headers(actor, replay_key),
        )
        listed = client.get(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
            headers=actor_headers(actor),
        )

        assert created.status_code == 201, created.text
        assert replay.status_code == 200, replay.text
        assert replay.json() == created.json()
        body = created.json()
        material = body["material"]
        assert body["draftRevision"] == draft["revision"] + 1
        assert material["displayName"] == "Материал 1"
        assert "synthetic-note" not in created.text
        assert "rawSha256" not in created.text
        assert listed.json() == {"items": [material]}

        downloaded = client.get(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials/{material['id']}",
            headers=actor_headers(actor),
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == raw
        assert "synthetic-note" not in downloaded.headers["content-disposition"]
        assert downloaded.headers["cache-control"] == "no-store"

        deleted = client.delete(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials/{material['id']}",
            headers=actor_headers(actor),
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["draftRevision"] == body["draftRevision"] + 1
        assert len(store.deleted) == 1
        assert client.get(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
            headers=actor_headers(actor),
        ).json() == {"items": []}


def test_material_rejects_unsafe_file_and_other_clinic_cannot_read_it() -> None:
    actor = 8_500_000_000 + uuid4().int % 100_000_000
    outsider = 8_600_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    seed_admin(outsider)
    store = FakeCaseMaterialStore()
    with application_client(case_material_store=store) as client:
        draft = _new_draft(client, actor)
        draft_id = draft["id"]
        rejected = client.post(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
            content=b"not an image",
            headers=_upload_headers(actor, uuid4(), "synthetic.jpg"),
        )
        identifier_rejected = client.post(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
            content=b"Synthetic note: call +7 (999) 123-45-67.",
            headers=_upload_headers(actor, uuid4()),
        )
        created = client.post(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
            content=b"Synthetic anonymised text.",
            headers=_upload_headers(actor, uuid4()),
        )
        material_id = created.json()["material"]["id"]
        foreign_download = client.get(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials/{material_id}",
            headers=actor_headers(outsider),
        )

    assert rejected.status_code == 422
    assert identifier_rejected.status_code == 422
    assert identifier_rejected.json()["error"]["code"] == (
        "CASE_MATERIAL_DIRECT_IDENTIFIER_NOT_ALLOWED"
    )
    assert created.status_code == 201, created.text
    assert not store.deleted
    assert foreign_download.status_code == 404
    assert foreign_download.json()["error"]["code"] == "INTAKE_DRAFT_NOT_FOUND"


def test_submission_moves_material_to_case_and_does_not_grant_access_by_clinic_role() -> None:
    actor = 8_700_000_000 + uuid4().int % 100_000_000
    other_admin = 8_800_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    seed_admin(other_admin)
    store = FakeCaseMaterialStore()
    with application_client(case_material_store=store) as client:
        draft = _new_draft(client, actor, version=1)
        draft_id = draft["id"]
        uploaded = client.post(
            f"/v1/telegram-intake-drafts/{draft_id}/case-materials",
            content=b"Synthetic anonymised note.",
            headers=_upload_headers(actor, uuid4()),
        )
        assert uploaded.status_code == 201, uploaded.text
        material_id = uploaded.json()["material"]["id"]
        submitted = client.post(
            f"/v1/telegram-case-workflows/{draft_id}/submissions",
            json=workflow_submission(),
            headers=actor_headers(actor),
        )
        assert submitted.status_code == 201, submitted.text
        case_id = submitted.json()["case"]["id"]
        owner_list = client.get(
            f"/v1/cases/{case_id}/case-materials", headers=actor_headers(actor)
        )
        owner_download = client.get(
            f"/v1/cases/{case_id}/case-materials/{material_id}", headers=actor_headers(actor)
        )
        foreign_download = client.get(
            f"/v1/cases/{case_id}/case-materials/{material_id}", headers=actor_headers(other_admin)
        )

    assert owner_list.status_code == 200
    assert [item["id"] for item in owner_list.json()["items"]] == [material_id]
    assert owner_download.status_code == 200
    assert foreign_download.status_code == 404
    assert foreign_download.json()["error"]["code"] == "CASE_NOT_FOUND"
