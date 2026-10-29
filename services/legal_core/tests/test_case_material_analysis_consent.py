"""Explicit new consent never rewrites the meaning of old upload receipts."""

import os
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from legal_core.case_api import _canonical_hash
from test_case_api import actor_headers, application_client, seed_admin
from test_case_materials_api import (
    FakeCaseMaterialStore, _new_draft, _upload_headers,
)
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

RAW = b"Synthetic anonymised case material: a review appointment was offered."
pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


def consent(material_id):
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection:
            return connection.scalar(text(
                "SELECT analysis_use_allowed FROM case_materials WHERE id=CAST(:id AS uuid)"
            ), {"id": material_id})
    finally:
        engine.dispose()


def audit_and_hash(material_id, key):
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection:
            return (
                connection.scalar(text(
                    "SELECT request_sha256 FROM idempotency_records WHERE key=:key"
                ), {"key": str(key)}),
                connection.scalar(text(
                    "SELECT count(*) FROM audit_events WHERE resource_id=CAST(:id AS uuid) "
                    "AND action='CASE_MATERIAL_ANALYSIS_CONSENT_GRANTED' "
                    "AND metadata_json=CAST(:metadata AS jsonb)"
                ), {"id": material_id, "metadata": '{"includeInAnalysis":true}'}),
            )
    finally:
        engine.dispose()


def test_new_upload_opts_in_only_when_explicitly_requested(isolated_database):
    actor = 93_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client(case_material_store=FakeCaseMaterialStore()) as client:
        draft = _new_draft(client, actor)
        path = f"/v1/telegram-intake-drafts/{draft['id']}/case-materials"
        created = client.post(path, params={"include_in_analysis": "true"}, content=RAW,
                              headers=_upload_headers(actor, uuid4()))
        assert created.status_code == 201, created.text
        assert created.json()["material"]["analysisUseAllowed"] is True
        assert consent(created.json()["material"]["id"]) is True


def test_old_default_and_receipt_replay_stay_storage_only_until_new_owned_intent(
    isolated_database,
):
    actor = 93_000_000_000 + uuid4().int % 100_000_000
    other = actor + 1_000_000_000
    seed_admin(actor)
    seed_admin(other)
    store = FakeCaseMaterialStore()
    with application_client(case_material_store=store) as client:
        draft = _new_draft(client, actor)
        path = f"/v1/telegram-intake-drafts/{draft['id']}/case-materials"
        old_key = uuid4()
        created = client.post(path, content=RAW, headers=_upload_headers(actor, old_key))
        assert created.status_code == 201, created.text
        original = created.json()
        assert "analysisUseAllowed" not in original["material"]
        material_id = original["material"]["id"]
        assert consent(material_id) is False
        old_hash, grants = audit_and_hash(material_id, old_key)
        assert old_hash == _canonical_hash({"rawSha256": hashlib.sha256(RAW).hexdigest(),
            "mimeType": "text/plain", "draftId": draft["id"]})
        assert grants == 0
        wrong_actor = client.post(path, params={"include_in_analysis": "true"}, content=RAW,
                                  headers=_upload_headers(other, uuid4()))
        assert wrong_actor.status_code == 404 and consent(material_id) is False
        same_key_changed = client.post(path, params={"include_in_analysis": "true"}, content=RAW,
                                       headers=_upload_headers(actor, old_key))
        assert same_key_changed.status_code == 422 and consent(material_id) is False
        assert same_key_changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        opted = client.post(path, params={"include_in_analysis": "true"}, content=RAW,
                            headers=_upload_headers(actor, uuid4()))
        assert opted.status_code == 200, opted.text
        assert opted.json()["material"]["analysisUseAllowed"] is True
        assert opted.json()["material"]["id"] == material_id and consent(material_id) is True
        assert opted.json()["draftRevision"] == original["draftRevision"] + 1
        replay = client.post(path, content=RAW, headers=_upload_headers(actor, old_key))
        assert replay.status_code == 200 and replay.json() == original
        assert audit_and_hash(material_id, old_key) == (old_hash, 1)
        assert len(store.objects) == 1
        archived = client.post(f"/v1/telegram-intake-drafts/{draft['id']}/archive",
            headers=actor_headers(actor, uuid4()),
            json={"expectedRevision": opted.json()["draftRevision"]})
        assert archived.status_code == 200, archived.text
        closed = client.post(path, params={"include_in_analysis": "true"}, content=RAW,
                             headers=_upload_headers(actor, uuid4()))
        assert closed.status_code in {404, 409}


def test_migration_keeps_old_files_false_and_refuses_erasing_existing_consent(isolated_database):
    actor = 93_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as client:
        draft = _new_draft(client, actor)
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    command.downgrade(config, "f42d9e6b0a31")
    material_id = uuid4()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO case_materials(id,clinic_id,draft_id,uploader_membership_id,"
                "display_name,mime_type,raw_size_bytes,raw_sha256,raw_object_key,expires_at) "
                "SELECT :id,clinic_id,id,actor_membership_id,'Материал 1','text/plain',1,"
                ":sha,:key,purge_after FROM telegram_intake_drafts WHERE id=CAST(:draft AS uuid)"
            ), {"id": material_id, "sha": "a" * 64, "key": f"synthetic-old/{material_id}",
                "draft": draft["id"]})
        command.upgrade(config, "head")
        assert consent(material_id) is False
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE case_materials SET analysis_use_allowed=true WHERE id=:id"
            ), {"id": material_id})
        with pytest.raises(Exception, match="cannot drop retained case material analysis consent"):
            command.downgrade(config, "f42d9e6b0a31")
    finally:
        engine.dispose()
