"""Schema checks only: synthetic hashes are not a claim of legal approval."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from legal_core.contracts import LegalFragmentResponse
from legal_core.lawyer_draft_contracts import (
    LawyerDraftContextRequest,
    LawyerDraftContextResponse,
)
from test_reports import _evidence


def _wire():
    case_id = str(uuid4())
    binding = {
        "asOfDate": "2026-08-31", "evidenceTraceSha256": "b" * 64,
        "clinicDocumentContextTraceSha256": "c" * 64, "riskPolicyVersion": "test.v4",
    }
    return {
        "schemaVersion": "lawyer-draft-context.v1",
        "source": {
            "operationId": str(uuid4()), "caseId": case_id,
            "factSnapshotSha256": "a" * 64, "context": binding,
        },
        "escalationId": str(uuid4()), "riskLevel": "HIGH",
        "context": {
            **binding, "caseId": case_id, "factSnapshotSha256": "a" * 64,
            "facts": {"eventSummary": "Синтетическая ситуация"},
            "evidence": [LegalFragmentResponse.model_validate(
                _evidence(), from_attributes=True,
            ).model_dump(mode="json", by_alias=True)],
            "highDemandThresholdKopecks": 1_000_000,
        },
    }


def test_private_context_round_trip_preserves_source_binding():
    result = LawyerDraftContextResponse.model_validate(_wire())
    assert LawyerDraftContextResponse.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("field,value", [
    ("caseId", str(uuid4())), ("factSnapshotSha256", "d" * 64),
    ("asOfDate", "2026-08-30"), ("evidenceTraceSha256", "e" * 64),
    ("clinicDocumentContextTraceSha256", "f" * 64), ("riskPolicyVersion", "test.v5"),
])
def test_response_rejects_source_context_mismatch(field, value):
    wire = _wire()
    target = wire["source"] if field in {"caseId", "factSnapshotSha256"} else (
        wire["source"]["context"]
    )
    target[field] = value
    with pytest.raises(ValidationError):
        LawyerDraftContextResponse.model_validate(wire)


def test_response_requires_retrieved_context_and_escalated_risk():
    for changes in ({"riskLevel": "LOW"}, {"source": {**_wire()["source"], "context": None}}):
        with pytest.raises(ValidationError):
            LawyerDraftContextResponse.model_validate({**_wire(), **changes})


@pytest.mark.parametrize("field,value", [
    ("clinicId", str(uuid4())), ("facts", {"text": "untrusted"}),
    ("files", ["untrusted"]), ("analysisAllowed", True), ("approveNorms", True),
    ("expectedFactSnapshotSha256", "invalid"), ("expectedRiskPolicyVersion", " "),
])
def test_request_is_metadata_only_and_rejects_invalid_bindings(field, value):
    request = {
        "expectedPreparationOperationId": str(uuid4()),
        "expectedFactSnapshotSha256": "a" * 64,
        "expectedRiskPolicyVersion": "test.v4",
    }
    valid = LawyerDraftContextRequest.model_validate(deepcopy(request))
    assert valid.expected_risk_policy_version == "test.v4"
    request[field] = value
    with pytest.raises(ValidationError):
        LawyerDraftContextRequest.model_validate(request)
