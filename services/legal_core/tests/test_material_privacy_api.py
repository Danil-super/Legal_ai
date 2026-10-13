"""Exercise privacy rejection before raw storage on both existing HTTP boundaries."""

import os
from uuid import uuid4

import pytest

from legal_core.clinic_document_parser import DOCX_MIME
from material_privacy_fixtures import (
    SYNTHETIC_EMAIL,
    directory_local_name_docx,
    docx_bytes,
    word_part,
)
from test_case_api import actor_headers, application_client, seed_admin
from test_case_materials_api import FakeCaseMaterialStore, _new_draft
from test_reference_evaluation_api import FakeReferenceEvaluationStore, _create_payload, _grant

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


@pytest.mark.parametrize("boundary", ["case", "reference"])
@pytest.mark.parametrize("location", ["header", "directory-local-name"])
def test_hidden_docx_identifier_is_rejected_before_object_storage(
    boundary: str, location: str,
) -> None:
    actor = 9_100_000_000 + uuid4().int % 100_000_000
    clinic_id, membership = seed_admin(actor)
    case_store = FakeCaseMaterialStore()
    reference_store = FakeReferenceEvaluationStore()
    if boundary == "reference":
        _grant(clinic_id, membership, "CONTRIBUTOR")
    raw = docx_bytes(extra_entries={"word/header1.xml": word_part("hdr", SYNTHETIC_EMAIL)})
    if location == "directory-local-name":
        raw = directory_local_name_docx(mismatched=True)
    with application_client(
        case_material_store=case_store, reference_evaluation_store=reference_store
    ) as client:
        if boundary == "case":
            draft = _new_draft(client, actor)
            endpoint = f"/v1/telegram-intake-drafts/{draft['id']}/case-materials"
            expected_code = "CASE_MATERIAL_DIRECT_IDENTIFIER_NOT_ALLOWED"
        else:
            created = client.post(
                "/v1/reference-evaluations",
                json=_create_payload(),
                headers=actor_headers(actor, uuid4()),
            )
            assert created.status_code == 201, created.text
            endpoint = f"/v1/reference-evaluations/{created.json()['case']['id']}/material"
            expected_code = "REFERENCE_EVALUATION_DIRECT_IDENTIFIER_NOT_ALLOWED"
        if location == "directory-local-name":
            expected_code = (
                "CASE_MATERIAL_FILE_INVALID" if boundary == "case" else
                "REFERENCE_EVALUATION_FILE_INVALID"
            )
        response = client.post(
            endpoint,
            content=raw,
            headers={
                **actor_headers(actor, uuid4()),
                "X-Source-Filename": "synthetic.docx",
                "Content-Type": DOCX_MIME,
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == expected_code
    assert SYNTHETIC_EMAIL not in response.text
    assert not case_store.objects
    assert not reference_store.objects


@pytest.mark.parametrize("boundary", ["case", "reference"])
def test_clean_docx_material_upload_replay_and_download_preserve_original(boundary: str) -> None:
    actor = 9_200_000_000 + uuid4().int % 100_000_000
    clinic_id, membership = seed_admin(actor)
    case_store = FakeCaseMaterialStore()
    reference_store = FakeReferenceEvaluationStore()
    if boundary == "reference":
        _grant(clinic_id, membership, "CONTRIBUTOR")
    raw = docx_bytes()
    with application_client(
        case_material_store=case_store, reference_evaluation_store=reference_store
    ) as client:
        if boundary == "case":
            draft = _new_draft(client, actor)
            endpoint = f"/v1/telegram-intake-drafts/{draft['id']}/case-materials"
        else:
            created = client.post(
                "/v1/reference-evaluations",
                json=_create_payload(),
                headers=actor_headers(actor, uuid4()),
            )
            assert created.status_code == 201, created.text
            endpoint = f"/v1/reference-evaluations/{created.json()['case']['id']}/material"
        headers = {
            **actor_headers(actor, uuid4()),
            "X-Source-Filename": "synthetic.docx",
            "Content-Type": DOCX_MIME,
        }
        uploaded = client.post(endpoint, content=raw, headers=headers)
        assert uploaded.status_code == 201, uploaded.text
        replay = client.post(endpoint, content=raw, headers=headers)
        assert replay.status_code == 200, replay.text
        assert replay.json() == uploaded.json()
        download_path = (
            f"{endpoint}/{uploaded.json()['material']['id']}" if boundary == "case" else endpoint
        )
        downloaded = client.get(download_path, headers=actor_headers(actor))
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == raw
        assert downloaded.headers["cache-control"] == "no-store"
        assert "synthetic.docx" not in downloaded.headers["content-disposition"]
    objects = case_store.objects if boundary == "case" else reference_store.objects
    assert len(objects) == 1
    assert next(iter(objects.values())) == (raw, DOCX_MIME)
