"""Urgent routing is durable; a missing AI plan must remain visible and actionable."""

import pytest
from pydantic import ValidationError

from legal_core.analysis_contracts import PreparationJobResponse
from test_case_preparation_contracts import _human_review, _limited


def payload(result=None, **changes):
    result = result or _human_review()
    return {"resultVersion": 2, "jobId": result["source"]["operationId"],
            "caseId": result["source"]["caseId"], "state": "SUCCEEDED", "result": result,
            **changes}


def test_missing_date_is_not_presented_as_a_completed_ai_plan():
    from telegram_gateway.analysis_jobs_runtime import JobSnapshot, job_message

    wire = payload(lawyerDraftAvailable=False, lawyerDraftErrorCode="ANALYSIS_DATE_UNCERTAIN")
    parsed = PreparationJobResponse.model_validate(wire)
    assert parsed.lawyer_draft_error_code == "ANALYSIS_DATE_UNCERTAIN"
    text, markup = job_message(JobSnapshot.parse(wire))
    assert "дат" in text.lower() and "не готов" in text.lower()
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert any(button.callback_data.startswith("case:clarify2:") for button in buttons)
    assert any(button.callback_data.startswith("case:escalation:") for button in buttons)


def test_ready_plan_opens_the_actual_ai_plan():
    from telegram_gateway.analysis_jobs_runtime import JobSnapshot, job_message

    text, markup = job_message(JobSnapshot.parse(payload(lawyerDraftAvailable=True)))
    assert "план" in text.lower() and "подготовлен" in text.lower()
    assert any("План ИИ" in button.text for row in markup.inline_keyboard for button in row)


@pytest.mark.parametrize("changes", [
    {"lawyerDraftAvailable": True, "lawyerDraftErrorCode": "ANALYSIS_DATE_UNCERTAIN"},
    {"lawyerDraftAvailable": False, "lawyerDraftErrorCode": "raw patient text"},
    {"lawyerDraftAvailable": "false"},
    {"state": "RUNNING", "result": None, "lawyerDraftAvailable": False},
])
def test_failure_metadata_cannot_claim_an_incoherent_success(changes):
    with pytest.raises(ValidationError):
        PreparationJobResponse.model_validate(payload(**changes))


def test_failure_metadata_is_not_valid_for_a_nonurgent_result():
    with pytest.raises(ValidationError):
        PreparationJobResponse.model_validate(payload(_limited(), lawyerDraftAvailable=False))
