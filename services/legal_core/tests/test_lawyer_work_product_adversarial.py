import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

from legal_core import lawyer_work_products
from test_case_api import actor_headers
from test_lawyer_draft_context import routed_case as routed_case
from test_lawyer_draft_source_freshness import wait_for_block
from test_lawyer_work_product_api import owner_role, plan_path, post, submission
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv('POSTGRES_INTEGRATION') != '1', reason='synthetic PG')


def ready_review(fixture):
    body = submission(fixture)
    created = post(fixture, body)
    assert created.status_code == 201, created.text
    owner_role(fixture)
    path = plan_path(fixture)
    client, actor = fixture[:2]
    claim = client.post(path.rsplit('/', 1)[0] + '/claim', headers=actor_headers(actor))
    assert claim.status_code == 200, claim.text
    review = {
        'expectedVersionId': created.json()['versionId'],
        'expectedContentSha256': created.json()['contentSha256'],
        'decision': 'REVISE',
        'actions': ['Сохраните синтетическую хронологию для проверки юристом.'],
    }
    return body, created.json(), review


def test_redirected_mutable_cache_cannot_replay_a_human_version_as_ai(routed_case):
    body, _, review = ready_review(routed_case)
    revised = routed_case[0].post(plan_path(routed_case) + '/reviews',
                                 headers=actor_headers(routed_case[1], uuid4()), json=review)
    assert revised.status_code == 201, revised.text
    with routed_case[9].begin() as db:
        db.execute(text("UPDATE idempotency_records SET response_json="
                        "jsonb_build_object('versionId',CAST(:id AS text)) "
                        "WHERE scope LIKE 'lawyer-product:submit:%'"),
                   {'id': revised.json()['versionId']})
    response = post(routed_case, body)
    assert response.status_code == 409, response.text


def test_ai_replay_after_human_review_never_replaces_the_human_version(routed_case):
    body, _, review = ready_review(routed_case)
    revised = routed_case[0].post(plan_path(routed_case) + '/reviews',
                                 headers=actor_headers(routed_case[1], uuid4()), json=review)
    assert revised.status_code == 201, revised.text
    replay = post(routed_case, body)
    assert replay.status_code == 200, replay.text
    assert replay.json()['origin'] == 'AI' and not replay.json()['isCurrent']
    latest = routed_case[0].get(plan_path(routed_case), headers=actor_headers(routed_case[1]))
    assert latest.status_code == 200, latest.text
    assert latest.json()['versionId'] == revised.json()['versionId']
    assert latest.json()['origin'] == 'HUMAN' and latest.json()['isCurrent']
    with routed_case[9].connect() as db:
        assert db.scalar(text('SELECT count(*) FROM case_work_product_versions')) == 2


def test_get_waits_for_real_human_review_commit_and_reads_the_new_latest(routed_case, monkeypatch):
    _, _, review = ready_review(routed_case)
    entered, release = threading.Event(), threading.Event()
    holder = []
    original = lawyer_work_products.recheck_submission_access

    async def pause_after_human_persistence(session, **kwargs):
        await original(session, **kwargs)
        human_version = await session.scalar(text(
            "SELECT id FROM case_work_product_versions WHERE case_id=:case AND origin='HUMAN' "
            'ORDER BY version DESC LIMIT 1'
        ), {'case': routed_case[4]})
        if human_version is not None:
            holder.append(await session.scalar(text('SELECT pg_backend_pid()')))
            entered.set()
            assert await asyncio.to_thread(release.wait, 10)

    monkeypatch.setattr(lawyer_work_products, 'recheck_submission_access',
                        pause_after_human_persistence)
    client, actor = routed_case[:2]
    path = plan_path(routed_case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(client.post, path + '/reviews',
                             headers=actor_headers(actor, uuid4()), json=review)
        reading = None
        try:
            assert entered.wait(10)
            reading = pool.submit(client.get, path, headers=actor_headers(actor))
            wait_for_block(routed_case[9], holder[0])
            assert not reading.done()
        finally:
            release.set()
        saved = saving.result(15)
        assert saved.status_code == 201, saved.text
        assert reading is not None
        viewed = reading.result(15)
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()['versionId'] == saved.json()['versionId']
    assert viewed.json()['content']['version'] == 2 and viewed.json()['isCurrent']
