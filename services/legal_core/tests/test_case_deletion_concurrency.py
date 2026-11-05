"""A request that saw an active case cannot write after confirmed deletion."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import create_engine, text

import legal_core.case_api as case_api
from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client
from test_lawyer_workspace import seed_escalation

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("action", ["discussion", "claim", "resolve"])
def test_deleted_case_rejects_lawyer_mutation_after_initial_access_read(monkeypatch, action):
    owner, escalation, _ = seed_escalation()
    engine = create_engine(owner_database_url())
    with engine.connect() as db:
        case_id = db.scalar(text("SELECT case_id FROM case_escalations WHERE id=:id"),
                            {"id": escalation})
    checked, resume = Event(), Event()
    original = case_api._discussion_escalation

    async def pause_after_access(*args, **kwargs):
        result = await original(*args, **kwargs)
        checked.set()
        assert await asyncio.to_thread(resume.wait, 10), "test failed to release access barrier"
        return result

    with application_client() as client:
        if action == "resolve":
            assert client.post(f"/v1/case-escalations/{escalation}/claim",
                               headers=actor_headers(owner)).status_code == 200
        monkeypatch.setattr(case_api, "_discussion_escalation", pause_after_access)
        with engine.connect() as db:
            before = db.scalar(text("SELECT count(*) FROM case_escalation_messages "
                                    "WHERE escalation_id=:id"), {"id": escalation})
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f"/v1/case-escalations/{escalation}/{action}",
                                  headers=actor_headers(owner),
                                  **({} if action == "claim" else
                                     {"json": {"body": "Учебный комментарий после проверки"}}))
            try:
                assert checked.wait(5), "request never reached its initial case access check"
                deleted = client.delete(f"/v1/case-history/{case_id}", headers=actor_headers(owner))
                assert deleted.status_code == 200, deleted.text
            finally:
                resume.set()
            result = pending.result(timeout=10)
            assert result.status_code == 410, result.text
        with engine.connect() as db:
            after = db.scalar(text("SELECT count(*) FROM case_escalation_messages "
                                   "WHERE escalation_id=:id"), {"id": escalation})
            assert before == after
    engine.dispose()
