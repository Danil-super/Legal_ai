"""Synthetic human declaration contracts, without legal approval side effects."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from legal_core.api_contracts import LegalGroupApprovalRequest


def test_group_current_copy_requires_both_explicit_checks() -> None:
    payload = dict(
        expectedSnapshot="a" * 64,
        versionIds=[UUID(int=75)],
        officialTextCompared=True,
        artifactIsComplete=True,
        effectiveDatesVerified=False,
        fragmentsVerified=True,
        currentCopyConfirmed=True,
        extractionLimitsUnderstood=True,
    )
    assert LegalGroupApprovalRequest.model_validate(payload).current_copy_confirmed
    for field in ("currentCopyConfirmed", "extractionLimitsUnderstood"):
        with pytest.raises(ValidationError):
            LegalGroupApprovalRequest.model_validate({**payload, field: False})


def test_group_legacy_date_check_is_not_inferred() -> None:
    with pytest.raises(ValidationError):
        LegalGroupApprovalRequest.model_validate(
            dict(
                expectedSnapshot="a" * 64,
                versionIds=[UUID(int=75)],
                officialTextCompared=True,
                artifactIsComplete=True,
                effectiveDatesVerified=False,
                fragmentsVerified=True,
            )
        )
