"""Owner-confirmed transfer is a strict, explicit optimistic-concurrency contract."""

from uuid import uuid4

import pytest

from legal_core import api_contracts


def test_transfer_requires_an_explicit_observed_assignment():
    model = api_contracts.EscalationAssignmentRequest
    target = str(uuid4())
    assert model.model_validate({
        "assignedMembershipId": target, "expectedAssignedMembershipId": None,
    }).assigned_membership_id.hex == target.replace("-", "")
    with pytest.raises(ValueError):
        model.model_validate({"assignedMembershipId": target})


@pytest.mark.parametrize("change", [
    {"assignedMembershipId": "invalid"}, {"expectedAssignedMembershipId": True},
    {"clinicId": str(uuid4())}, {"actorMembershipId": str(uuid4())},
])
def test_transfer_rejects_malformed_or_client_supplied_authority(change):
    with pytest.raises(ValueError):
        api_contracts.EscalationAssignmentRequest.model_validate({
            "assignedMembershipId": str(uuid4()),
            "expectedAssignedMembershipId": None, **change,
        })
