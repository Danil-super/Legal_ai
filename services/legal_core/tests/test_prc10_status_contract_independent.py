"""Public operation states cannot smuggle proposal or acceptance privileges."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from legal_core.intake_preparation_contracts import PreparationStatusResponse


@pytest.mark.parametrize("state", ["PENDING", "PROPOSED", "MANUAL_CONTINUATION", "ACCEPTED"])
@pytest.mark.parametrize("tamper", ["suggestions", "source", "acceptance", "private"])
def test_operation_state_rejects_incoherent_public_payload(state, tamper):
    source = {"draftId": str(uuid4()), "draftRevision": 2,
              "inputSha256": "a" * 64, "proposalSha256": "b" * 64}
    suggestions = {"sourceSpans": {}}
    acceptance = {"draftId": source["draftId"], "acceptedRevision": 3}
    data = {"operationId": str(uuid4()), "state": state,
            "suggestions": suggestions if state == "PROPOSED" else None,
            "source": source if state == "PROPOSED" else None,
            "acceptance": acceptance if state == "ACCEPTED" else None}
    PreparationStatusResponse.model_validate(data)
    if tamper == "private":
        data["leaseToken"] = str(uuid4())
    else:
        data[tamper] = None if data[tamper] is not None else {
            "suggestions": suggestions, "source": source, "acceptance": acceptance,
        }[tamper]
    with pytest.raises(ValidationError):
        PreparationStatusResponse.model_validate(data)
