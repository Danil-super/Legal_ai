"""Access must still hold after a submission waits for its case lock."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from test_preparation_analysis import _count, _submission
from test_preparation_analysis import isolated_database as isolated_database
from test_preparation_analysis import prepared_case as prepared_case

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("withdrawal", ["membership", "user", "subscription", "natural_expiry",
                                       "case_expiry"])
def test_submission_rechecks_access_and_retention_after_waiting(
    prepared_case, monkeypatch, version, withdrawal,
):
    import legal_core.analysis_api as api

    client, actor, _, case, headers, context, engine = prepared_case
    entered, release = threading.Event(), threading.Event()
    original = api._tenant_case

    async def pause_before_case_lock(*args, **kwargs):
        if kwargs.get("for_update"):
            entered.set()
            assert await asyncio.to_thread(release.wait, 10)
        return await original(*args, **kwargs)

    monkeypatch.setattr(api, "_tenant_case", pause_before_case_lock)

    def submit():
        route = "preparation-submissions" if version == 2 else "analysis-submissions"
        return client.post(f"/v1/cases/{case}/{route}", headers=headers,
                           json=_submission(context))

    statements = {
        "membership": "UPDATE clinic_users SET status='REVOKED' WHERE user_id="
        "(SELECT id FROM users WHERE telegram_user_id=:actor)",
        "user": "UPDATE users SET status='REVOKED' WHERE telegram_user_id=:actor",
        "subscription": "UPDATE subscription_entitlements SET status='SUSPENDED' WHERE user_id="
        "(SELECT id FROM users WHERE telegram_user_id=:actor)",
        # Ends after the request's transaction began, but before it resumes. now()
        # alone would still consider this active; the final gate needs wall time.
        "natural_expiry": "UPDATE subscription_entitlements SET ends_at=clock_timestamp() "
        "-interval '1 microsecond' WHERE user_id="
        "(SELECT id FROM users WHERE telegram_user_id=:actor)",
        "case_expiry": "UPDATE cases SET retention_due_at=clock_timestamp() "
        "-interval '1 second' WHERE id=:case",
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(submit)
        try:
            assert entered.wait(10)
            with engine.begin() as connection:
                connection.execute(text(statements[withdrawal]), {"actor": actor, "case": case})
        finally:
            release.set()
        response = future.result(15)
    assert response.status_code == (410 if withdrawal == "case_expiry" else 403), response.text
    with engine.connect() as connection:
        assert _count(connection, "case_reports", case) == 0
        assert _count(connection, "case_analysis_runs", case) == 0
