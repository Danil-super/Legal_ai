import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import text

from test_lawyer_draft_context import routed_case as routed_case, read
from test_lawyer_draft_context import isolated_database as isolated_database
from test_case_api import actor_headers


def clinic_document(routed_case):
    client, actor = routed_case[:2]
    headers = actor_headers(actor)
    doc = client.post('/v1/clinic-documents', headers=headers, json={
        'documentKey': 'synthetic-review-probe', 'documentType': 'CONTRACT',
        'title': 'Синтетический договор',
    })
    assert doc.status_code == 201, doc.text
    version = client.post(f"/v1/clinic-documents/{doc.json()['id']}/text-versions",
                          headers=headers, json={
        'sourceFilename': 'synthetic.txt',
        'normalizedText': 'Синтетический договор: согласие и гарантии, возврат.',
        'validFrom': '2026-01-01',
    })
    assert version.status_code == 201, version.text
    path = f"/v1/clinic-documents/versions/{version.json()['id']}/approval-events"
    assert client.post(path, headers=headers, json={
        'decision': 'APPROVED', 'reasonCode': 'SYNTHETIC_REVIEW_PASSED',
    }).status_code == 201
    return path, version.json()['id']


def test_withdrawn_clinic_context_not_returned_after_retrieval(routed_case, monkeypatch):
    import legal_core.lawyer_draft_context as context_api

    client, actor = routed_case[:2]
    headers = actor_headers(actor)
    path, _ = clinic_document(routed_case)
    entered, release = threading.Event(), threading.Event()
    original = context_api._retrieve_analysis_state

    async def pause(*args, **kwargs):
        state = await original(*args, **kwargs)
        assert state.clinic_document_context
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return state

    monkeypatch.setattr(context_api, '_retrieve_analysis_state', pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read, routed_case)
        try:
            assert entered.wait(10)
            retired = client.post(path, headers=headers, json={
                'decision': 'RETIRED', 'reasonCode': 'SYNTHETIC_REVIEW_WITHDRAWN',
            })
            assert retired.status_code == 201, retired.text
            visible = client.get('/v1/clinic-documents/fragments', headers=headers,
                                 params={'query': 'договор', 'as_of_date': '2026-09-12'})
            assert visible.status_code == 200 and visible.json()['items'] == []
        finally:
            release.set()
        response = future.result(15)
    assert response.status_code == 409, response.text


def test_withdrawn_legal_reviewer_not_returned_after_retrieval(routed_case, monkeypatch):
    import legal_core.lawyer_draft_context as context_api

    entered, release = threading.Event(), threading.Event()
    original = context_api._retrieve_analysis_state

    async def pause(*args, **kwargs):
        state = await original(*args, **kwargs)
        assert state.evidence
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return state

    monkeypatch.setattr(context_api, '_retrieve_analysis_state', pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read, routed_case)
        try:
            assert entered.wait(10)
            with routed_case[9].begin() as connection:
                connection.execute(text("UPDATE users SET system_role=NULL "
                                        "WHERE system_role='LEGAL_EDITOR'"))
                assert connection.scalar(text(
                    'SELECT count(*) FROM production_legal_fragments')) == 0
        finally:
            release.set()
        response = future.result(15)
    assert response.status_code == 409, response.text


def wait_for_block(owner, blocker_pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with owner.connect() as observer:
            if observer.scalar(text(
                'SELECT EXISTS(SELECT 1 FROM pg_stat_activity '
                'WHERE :blocker=ANY(pg_blocking_pids(pid)))'
            ), {'blocker': blocker_pid}):
                return
        time.sleep(0.01)
    pytest.fail('the operation must actually wait on the held provenance lock')


@pytest.mark.parametrize('source', ['clinic', 'legal'])
def test_guard_holds_selected_provenance_until_callers_transaction_ends(
    routed_case, monkeypatch, source,
):
    import legal_core.lawyer_draft_context as context_api

    path, _ = clinic_document(routed_case)
    entered, release = threading.Event(), threading.Event()
    holder = []
    original = context_api.require_lawyer_draft_sources_current

    async def pause(session, **kwargs):
        await original(session, **kwargs)
        holder.append(await session.scalar(text('SELECT pg_backend_pid()')))
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)

    def withdraw():
        if source == 'clinic':
            response = routed_case[0].post(path, headers=actor_headers(routed_case[1]), json={
                'decision': 'RETIRED', 'reasonCode': 'SYNTHETIC_WITHDRAWAL',
            })
            assert response.status_code == 201, response.text
        else:
            with routed_case[9].begin() as connection:
                connection.execute(text("UPDATE users SET system_role=NULL "
                                        "WHERE system_role='LEGAL_EDITOR'"))

    monkeypatch.setattr(context_api, 'require_lawyer_draft_sources_current', pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        read_future = pool.submit(read, routed_case)
        withdrawal = None
        try:
            assert entered.wait(10)
            withdrawal = pool.submit(withdraw)
            wait_for_block(routed_case[9], holder[0])
            assert not withdrawal.done()
        finally:
            release.set()
        response = read_future.result(15)
        if withdrawal is not None:
            withdrawal.result(15)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('fault', ['source', 'membership', 'lease'])
def test_selected_legal_source_lock_wait_rechecks_withdrawal_and_access(routed_case, fault):
    # The corpus view can initially see an active reviewer while that user's
    # update is uncommitted. The final provenance lock must wait and then check.
    owner = routed_case[9]
    if fault == 'lease':
        with owner.begin() as connection:
            connection.execute(text('UPDATE analysis_jobs '
                                    "SET lease_until=clock_timestamp()+interval '3 seconds' "
                                    'WHERE id=:id'), {'id': routed_case[5].id})
    with owner.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = blocker.begin()
        blocker_pid = blocker.scalar(text('SELECT pg_backend_pid()'))
        blocker.execute(text("SELECT id FROM users WHERE system_role='LEGAL_EDITOR' FOR UPDATE"))
        future = pool.submit(read, routed_case)
        try:
            wait_for_block(owner, blocker_pid)
            if fault == 'source':
                blocker.execute(text("UPDATE users SET system_role=NULL "
                                     "WHERE system_role='LEGAL_EDITOR'"))
            elif fault == 'membership':
                blocker.execute(text("UPDATE clinic_users SET status='REVOKED' WHERE id=:id"),
                                {'id': routed_case[3]})
            else:
                # The loader owns the job row. Let wall-clock expiry happen,
                # rather than trying to mutate the row from the other session.
                time.sleep(3.1)
            transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
        response = future.result(15)
    assert response.status_code == (403 if fault == 'membership' else 409), response.text


@pytest.mark.parametrize('trace', ['evidence_trace_sha256', 'clinic_document_context_trace_sha256'])
def test_final_guard_rejects_changed_exact_trace(routed_case, monkeypatch, trace):
    import legal_core.lawyer_draft_context as context_api

    original = context_api._retrieve_analysis_state

    async def tamper(*args, **kwargs):
        return replace(await original(*args, **kwargs), **{trace: 'f' * 64})

    monkeypatch.setattr(context_api, '_retrieve_analysis_state', tamper)
    response = read(routed_case)
    assert response.status_code == 409, response.text


pytestmark = pytest.mark.skipif(os.getenv('POSTGRES_INTEGRATION') != '1',
                              reason='requires disposable PostgreSQL')
