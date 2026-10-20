import asyncio
import os

import pytest

from legal_core.case_api import ApiError
from legal_core.intake_answer_operations import cancel_answer
from legal_core.intake_draft_staging import stage_answer, stage_preparation
from legal_core.intake_preparation import cancel_preparation
from test_case_api import application_client
from test_intake_draft_staging import preparation, sql, stage
from test_intake_preparation_lifecycle import _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]


@pytest.mark.parametrize("kind", ["initial", "answer"])
def test_review_staging_replay_rejects_changed_original_proposal_audit(draft_owner, kind):
    status, request = preparation(draft_owner, kind)
    with application_client() as client:
        assert stage(client, draft_owner[0], status, request, kind).status_code == 200
        sql("UPDATE audit_events SET metadata_json='{}'::jsonb "
            "WHERE resource_id=:operation AND action IN "
            "('INTAKE_PREPARATION_PROPOSED','INTAKE_ANSWER_PROPOSED')",
            {"operation": status.operation_id})
        assert stage(client, draft_owner[0], status, request, kind).status_code == 409


@pytest.mark.parametrize("kind", ["initial", "answer"])
def test_review_cancel_and_stage_linearize_without_acceptance_or_cases(draft_owner, kind):
    status, request = preparation(draft_owner, kind)
    stage_action = stage_answer if kind == "answer" else stage_preparation
    cancel_action = cancel_answer if kind == "answer" else cancel_preparation

    async def race(sessions):
        return await asyncio.gather(
            stage_action(sessions, telegram_user_id=draft_owner[0],
                         operation_id=status.operation_id, request=request),
            cancel_action(sessions, telegram_user_id=draft_owner[0],
                          operation_id=status.operation_id),
            return_exceptions=True,
        )

    results = _run(race)
    assert sum(isinstance(result, ApiError) for result in results) == 1
    assert all(not isinstance(result, BaseException) or isinstance(result, ApiError)
               for result in results)
    staged = not isinstance(results[0], ApiError)
    assert sql("SELECT count(*) FROM audit_events WHERE resource_id=:id "
               "AND action='INTAKE_PROPOSAL_STAGED'", {"id": status.operation_id}) == int(staged)
    assert sql("SELECT count(*) FROM audit_events WHERE resource_id=:id "
               "AND action IN ('INTAKE_PREPARATION_ACCEPTED','INTAKE_ANSWER_ACCEPTED')",
               {"id": status.operation_id}) == 0
    for table in ("cases", "case_facts", "case_reports"):
        assert sql(f"SELECT count(*) FROM {table} WHERE clinic_id=:id",
                   {"id": draft_owner[1]}) == 0
