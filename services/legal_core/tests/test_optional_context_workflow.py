"""Real submission omits optional context without clearing evidence or risk gates."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_case_api import actor_headers
from test_report_free_workflow import _payload
from test_report_free_workflow import preparation_workflow as preparation_workflow
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("amount", [None, 500_000, 1_000_000])
def test_absent_context_confirms_once_and_does_not_replace_risk_or_evidence(preparation_workflow,
                                                                           amount):
    core, actor, _, _, engine = preparation_workflow
    payload = _payload()
    payload["facts"] = [item for item in payload["facts"]
                        if item["factKey"] not in {"CONFLICT_STAGE", "CLINIC_ACTIONS"}]
    if amount is not None:
        screening = next(item for item in payload["facts"]
                         if item["factKey"] == "FACTUAL_SAFETY_SCREENING")["value"]
        screening.update(moneyRequested="YES", amount={"amountKopecks": amount,
                                                       "currency": "RUB"})
    path = f"/v1/telegram-case-workflows/{uuid4()}/submissions"
    created = core.post(path, headers=actor_headers(actor), json=payload)
    assert created.status_code == 201, created.text
    case_id = created.json()["case"]["id"]
    replay = core.post(path, headers=actor_headers(actor), json=payload)
    assert replay.status_code == 200 and replay.json() == created.json(), replay.text
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM cases")) == 1
        assert connection.scalar(text("SELECT count(*) FROM case_facts WHERE case_id=:id "
                                      "AND fact_key IN ('CONFLICT_STAGE','CLINIC_ACTIONS')"),
                                 {"id": case_id}) == 0
    result = core.post(f"/v1/cases/{case_id}/preparation-context", headers={
        **actor_headers(actor, uuid4()), "X-Agent-Internal-Key": INTERNAL_KEY,
    })
    assert result.status_code == 201, result.text
    if amount == 1_000_000:
        assert result.json()["status"] == "HUMAN_REVIEW"
        assert result.json()["riskLevel"] == "HIGH"
    else:
        # An omitted contextual detail is not a substitute for approved legal evidence.
        assert result.json()["status"] == "LIMITED"
        assert result.json()["reasonCode"] == "LEGAL_EVIDENCE_UNAVAILABLE"
