"""Report-free recovery of fictional cases preserves confirmed history and attempts."""

from copy import deepcopy
import os
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from legal_core.api_contracts import CorrectionSource
from legal_core.analysis_contracts import PreparationResult
from legal_core.case_api import _canonical_hash
from legal_core.case_clarification import ClarificationDraftRequest, _prefill
from legal_core.contracts import FactKey
from test_case_api import actor_headers, application_client, seed_admin
from test_case_materials_api import FakeCaseMaterialStore, _new_draft, _upload_headers
from test_clarification_recovery import _confirm, _guided_data, _source_rows
from test_factual_safety_intake import screening
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case
from test_preparation_precontext import frozen_precontext as frozen_precontext
from test_risk_policy_v4_postgres import activate_v4
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

requires_pg = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                 reason="requires disposable PostgreSQL")


def _binding(result):
    source = result["source"]
    return {"expectedFactSnapshotSha256": source["factSnapshotSha256"],
            "expectedOperationId": source["operationId"],
            "expectedOperationSha256": _canonical_hash(result)}


def _preview(client, actor, case, operation):
    return client.get(f"/v1/cases/{case}/clarification-drafts/preview",
                      headers=actor_headers(actor), params={"operation_id": operation})


def _recover(client, actor, case, result, key=None):
    return client.post(f"/v1/cases/{case}/clarification-drafts",
                       headers=actor_headers(actor, key or uuid4()), json=_binding(result))


def _late(prepared, **options):
    client, _, _, case, headers, context, _ = prepared
    response = client.post(f"/v1/cases/{case}/preparation-submissions", headers=headers,
                           json=_submission(context, **options))
    assert response.status_code == 201, response.text
    return response.json()


def test_operation_origin_roundtrips_without_adding_nulls_to_legacy_hashes():
    operation = str(uuid4())
    request = {"expectedFactSnapshotSha256": "a" * 64,
               "expectedOperationId": operation, "expectedOperationSha256": "b" * 64}
    assert ClarificationDraftRequest.model_validate(request).model_dump(
        mode="json", by_alias=True
    ) == request
    source = {"caseId": str(uuid4()), "factSnapshotSha256": "a" * 64,
              "operationId": operation, "operationSha256": "b" * 64}
    assert CorrectionSource.model_validate(source).model_dump(mode="json", by_alias=True) == source
    for value in ({"expectedFactSnapshotSha256": "a" * 64},
                  {"expectedFactSnapshotSha256": "a" * 64,
                   "expectedReportId": str(uuid4()), "expectedReportSha256": "b" * 64}):
        assert ClarificationDraftRequest.model_validate(value).model_dump(
            mode="json", by_alias=True
        ) == value


@pytest.mark.parametrize("change", [
    {"expectedOperationId": str(uuid4())}, {"expectedOperationSha256": "b" * 64},
    {"expectedOperationId": str(uuid4()), "expectedOperationSha256": "bad"},
    {"expectedOperationId": str(uuid4()), "expectedOperationSha256": "b" * 64,
     "expectedReportId": str(uuid4()), "expectedReportSha256": "c" * 64},
])
def test_origin_rejects_unpaired_malformed_and_mixed_bindings(change):
    with pytest.raises(ValidationError):
        ClarificationDraftRequest.model_validate({"expectedFactSnapshotSha256": "a" * 64,
                                                  **change})
    source_change = {key[8].lower() + key[9:]: value for key, value in change.items()}
    with pytest.raises(ValidationError):
        CorrectionSource.model_validate({"caseId": str(uuid4()),
                                         "factSnapshotSha256": "a" * 64, **source_change})


@pytest.mark.parametrize("questions", [True, False])
@requires_pg
def test_report_free_result_opens_one_unconfirmed_draft_without_changing_history(
    prepared_case, questions,
):
    client, actor, _, case, headers, _, _ = prepared_case
    result = _late(prepared_case, questions=questions)
    before = _source_rows(case)
    preview = _preview(client, actor, case, result["source"]["operationId"])
    assert preview.status_code == 200, preview.text
    assert preview.json() == {"caseId": case, **_binding(result)}
    key = uuid4()
    recovered = _recover(client, actor, case, result, key)
    assert recovered.status_code == 201, recovered.text
    draft = recovered.json()
    assert draft["wizardState"] == ("ANALYSIS_CLARIFICATION" if questions else "V2_CONFIRM")
    assert draft["draftData"]["correctionSource"] == {
        "caseId": case, "factSnapshotSha256": result["source"]["factSnapshotSha256"],
        "operationId": result["source"]["operationId"],
        "operationSha256": _canonical_hash(result),
    }
    assert draft["draftData"].get("analysisClarificationRequests", []) == (
        result.get("clarificationRequests", [])
    )
    replay = _recover(client, actor, case, result, key)
    assert replay.status_code == 200 and replay.json() == draft
    assert client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                      headers=actor_headers(actor)).json() == draft
    assert _source_rows(case) == before
    assert _source_rows(case)["case_reports"] == []
    saved = deepcopy(draft["draftData"])
    saved["correctionSource"]["operationSha256"] = "0" * 64
    rejected = client.put(f"/v1/telegram-intake-drafts/{draft['id']}", headers=headers,
                          json={"expectedRevision": 1, "wizardState": draft["wizardState"],
                                "draftData": saved})
    assert rejected.status_code == 409, rejected.text


@pytest.mark.parametrize(("date", "safety", "state"), [
    (None, screening(), "EVENT_DATE"),
    ({"date": None, "precision": "UNKNOWN"}, None, "SAFETY"),
    ({"date": "2026-09-01", "precision": "APPROXIMATE"}, screening(), "V2_CONFIRM"),
])
def test_new_prefill_selects_only_unattempted_steps(date, safety, state):
    facts = {FactKey.INTAKE_VERSION: "GUIDED_V2"}
    if date is not None:
        facts[FactKey.EVENT_DATE] = date
    if safety is not None:
        facts[FactKey.FACTUAL_SAFETY_SCREENING] = safety
    attempted = {"answers": [{"questionId": "RESPONSE_PURPOSE", "value": "UNKNOWN"}]}
    facts[FactKey.ANALYSIS_CLARIFICATION_ANSWERS] = attempted
    result = PreparationResult.model_validate({
        "resultVersion": 2, "status": "LIMITED", "reasonCode": "INSUFFICIENT_FACTS",
        "missingFactKeys": [], "source": {"operationId": str(uuid4()),
        "caseId": str(uuid4()), "factSnapshotSha256": "a" * 64, "context": None},
    })
    actual, data = _prefill(facts, result.root.source.case_id, "a" * 64,
                             source_preparation=result)
    assert actual == state
    assert data["analysisClarificationAnswers"] == attempted


@requires_pg
def test_recovery_keeps_materials_and_original_download_acl_unchanged(isolated_database):
    actor = 83_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    activate_v4()
    store = FakeCaseMaterialStore()
    with application_client(case_material_store=store) as client:
        draft = _new_draft(client, actor)
        upload = client.post(f"/v1/telegram-intake-drafts/{draft['id']}/case-materials",
                             headers=_upload_headers(actor, uuid4()),
                             content=b"Synthetic anonymised note.")
        assert upload.status_code == 201, upload.text
        data = _guided_data() | {"incomingSourceStatus": "ATTACHED",
                                 "caseMaterialsStatus": "ATTACHED"}
        workflow = _confirm(client, actor, data, UUID(draft["id"]))
        case = workflow["case"]["id"]
        result = client.post(f"/v1/cases/{case}/preparation-context", headers={
            **actor_headers(actor, uuid4()), "X-Agent-Internal-Key": INTERNAL_KEY,
        })
        assert result.status_code == 201, result.text
        before, objects = _source_rows(case), deepcopy(store.objects)
        recovered = _recover(client, actor, case, result.json())
        assert recovered.status_code == 201, recovered.text
        new = recovered.json()
        assert new["draftData"]["incomingSourceStatus"] == "NOT_ATTACHED"
        assert new["draftData"]["caseMaterialsStatus"] == "NOT_ATTACHED"
        material = before["case_materials"][0]["id"]
        assert client.get(f"/v1/cases/{case}/case-materials/{material}",
                          headers=actor_headers(actor)).status_code == 200
        assert client.get(f"/v1/telegram-intake-drafts/{new['id']}/case-materials/{material}",
                          headers=actor_headers(actor)).status_code == 404
        assert client.get(f"/v1/telegram-intake-drafts/{new['id']}/case-materials",
                          headers=actor_headers(actor)).json() == {"items": []}
        assert _source_rows(case) == before
        assert store.objects == objects and store.deleted == []


@pytest.mark.parametrize("frozen_precontext", [
    {"eventDate": {"date": None, "precision": "UNKNOWN"}},
    {"eventDate": {"date": "2026-09-01", "precision": "APPROXIMATE"}},
    {"safetyScreening": screening(hospitalizationReported="UNKNOWN")},
    {},
], indirect=True)
@requires_pg
def test_precontext_recovery_preserves_attempts_without_automatically_reasking(
    frozen_precontext,
):
    client, actor, case, headers, _ = frozen_precontext
    response = client.post(f"/v1/cases/{case}/preparation-context", headers=headers)
    assert response.status_code == 201, response.text
    result = response.json()
    before = _source_rows(case)
    recovered = _recover(client, actor, case, result)
    assert recovered.status_code == 201, recovered.text
    assert recovered.json()["wizardState"] == "V2_CONFIRM"
    data = recovered.json()["draftData"]
    original = {row["fact_key"]: row["value_json"] for row in before["case_facts"]}
    assert data["eventDate"] == original["EVENT_DATE"]
    assert data["safetyScreening"] == original["FACTUAL_SAFETY_SCREENING"]
    assert _source_rows(case) == before
