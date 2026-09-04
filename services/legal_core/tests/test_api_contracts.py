import pytest
from legal_core.api_contracts import (
    AddFactsRequest,
    ClinicMemberCreateRequest,
    EscalationDiscussionMessageRequest,
    FactInput,
    PlatformSubscriptionGrantRequest,
    TelegramIntakeDraftUpdateRequest,
)
from pydantic import ValidationError


def test_fact_input_rejects_a_value_type_that_does_not_match_its_fact_key() -> None:
    with pytest.raises(ValidationError, match="INCIDENT_TYPES"):
        FactInput(
            factKey="INCIDENT_TYPES",
            valueType="TEXT",
            value={"text": "Произвольный текст вместо набора типов"},
            sourceType="USER_STATEMENT",
        )


def test_fact_input_rejects_invalid_date_shape_even_when_it_is_not_empty() -> None:
    with pytest.raises(ValidationError, match="SERVICE_DATE"):
        FactInput(
            factKey="SERVICE_DATE",
            valueType="DATE",
            value={"date": "2026-02-30", "precision": "EXACT"},
            sourceType="USER_STATEMENT",
        )


def test_add_facts_request_rejects_duplicate_fact_keys() -> None:
    duplicate_fact = {
        "factKey": "SERVICE_TYPE",
        "valueType": "TEXT",
        "value": {"text": "Профессиональная гигиена"},
        "sourceType": "USER_STATEMENT",
    }

    with pytest.raises(ValidationError, match="fact keys must be unique"):
        AddFactsRequest(
            questionId="service_type",
            intakeSchemaVersion="dental-case-intake.v1",
            facts=[duplicate_fact, duplicate_fact],
        )


def test_fact_input_preserves_an_explicit_unknown_signal() -> None:
    fact = FactInput(
        factKey="HARM_CLAIMED",
        valueType="BOOLEAN",
        value={"state": "UNKNOWN"},
        sourceType="USER_STATEMENT",
    )

    assert fact.value == {"state": "UNKNOWN"}


def test_fact_input_preserves_an_explicit_unknown_date() -> None:
    fact = FactInput(
        factKey="SERVICE_DATE",
        valueType="DATE",
        value={"date": None, "precision": "UNKNOWN"},
        sourceType="USER_STATEMENT",
    )

    assert fact.value == {"date": None, "precision": "UNKNOWN"}


def test_fact_input_rejects_a_date_value_when_precision_is_unknown() -> None:
    with pytest.raises(ValidationError, match="SERVICE_DATE"):
        FactInput(
            factKey="SERVICE_DATE",
            valueType="DATE",
            value={"date": "2026-08-22", "precision": "UNKNOWN"},
            sourceType="USER_STATEMENT",
        )


def test_free_pilot_grant_requires_a_bounded_explicit_duration() -> None:
    request = PlatformSubscriptionGrantRequest(
        telegramUserId=7_000_000_002,
        planCode="FREE_PILOT",
        pilotDays=30,
    )

    assert request.plan_code == "FREE_PILOT"
    assert request.pilot_days == 30

    with pytest.raises(ValidationError, match="pilotDays"):
        PlatformSubscriptionGrantRequest(
            telegramUserId=7_000_000_002,
            planCode="FREE_PILOT",
        )

    with pytest.raises(ValidationError, match="pilotDays"):
        PlatformSubscriptionGrantRequest(
            telegramUserId=7_000_000_002,
            planCode="MVP_MANUAL",
            pilotDays=30,
        )

    with pytest.raises(ValidationError):
        PlatformSubscriptionGrantRequest(
            telegramUserId=7_000_000_002,
            planCode="FREE_PILOT",
            pilotDays=91,
        )


def test_clinic_member_and_escalation_discussion_contracts_are_bounded() -> None:
    member = ClinicMemberCreateRequest(telegramUserId=7_000_000_003, role="CLINIC_LAWYER")
    message = EscalationDiscussionMessageRequest(body="  Уточните дату получения претензии.  ")

    assert member.role == "CLINIC_LAWYER"
    assert message.body == "Уточните дату получения претензии."

    with pytest.raises(ValidationError):
        EscalationDiscussionMessageRequest(body=" " * 5)


def test_intake_draft_update_accepts_only_a_bounded_known_snapshot() -> None:
    update = TelegramIntakeDraftUpdateRequest(
        expectedRevision=1,
        wizardState="SERVICE_TYPE",
        draftData={"incident_type": "QUALITY_COMPLAINT"},
    )

    assert update.expected_revision == 1
    assert update.draft_data == {"incident_type": "QUALITY_COMPLAINT"}

    with pytest.raises(ValidationError, match="expectedRevision"):
        TelegramIntakeDraftUpdateRequest(
            expectedRevision=0,
            wizardState="SERVICE_TYPE",
            draftData={},
        )

    with pytest.raises(ValidationError, match="draftData"):
        TelegramIntakeDraftUpdateRequest(
            expectedRevision=1,
            wizardState="SERVICE_TYPE",
            draftData={"unexpected": "value"},
        )
