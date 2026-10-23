"""Private temporal context accepts a calendar date, not a client clock or timestamp."""

from datetime import date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from legal_core.intake_preparation_contracts import (
    IntakeUnderstandingContext,
    UnderstandingContextRequest,
)


def context_payload():
    return {
        "operationId": str(uuid4()), "draftId": str(uuid4()), "draftRevision": 2,
        "inputSha256": "a" * 64, "operationKind": "UNDERSTAND_INTAKE",
        "source": "Вымышленное обращение по договору клиники.",
    }


@pytest.mark.parametrize("value", ["2026-10-20", date(2026, 10, 20)])
def test_context_accepts_only_explicit_calendar_reference(value):
    context = IntakeUnderstandingContext.model_validate(context_payload() | {"asOfDate": value})
    assert context.as_of_date == date(2026, 10, 20)
    assert context.model_dump(mode="json", by_alias=True)["asOfDate"] == "2026-10-20"


def test_legacy_context_does_not_invent_a_reference_date():
    context = IntakeUnderstandingContext.model_validate(context_payload())
    assert context.as_of_date is None


@pytest.mark.parametrize("value", [
    True, 0, 1_792_454_400, 1_792_454_400.0, b"2026-10-20", "20261020",
    "2026-10-20T00:00:00", "2026-02-30", datetime(2026, 10, 20),
])
def test_context_refuses_timestamp_coercion_and_invalid_dates(value):
    with pytest.raises(ValidationError):
        IntakeUnderstandingContext.model_validate(context_payload() | {"asOfDate": value})


def test_caller_cannot_choose_reference_date():
    with pytest.raises(ValidationError):
        UnderstandingContextRequest.model_validate({
            "leaseToken": str(uuid4()), "expectedRevision": 2,
            "expectedInputSha256": "a" * 64, "asOfDate": "2026-10-20",
        })
