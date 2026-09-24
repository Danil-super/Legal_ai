"""Separate, immutable personal contracts. Not aliases for clinical facts or roles."""

from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PersonalContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Audience(StrEnum):
    PATIENT = "PATIENT"
    EMPLOYEE = "EMPLOYEE"


class Realm(StrEnum):
    PERSONAL = "PERSONAL"
    CLINIC = "CLINIC"


class Topic(StrEnum):
    PATIENT_DOCUMENTS = "patient_documents"
    PATIENT_QUALITY = "patient_quality"
    PATIENT_CANCELLATION = "patient_cancellation"
    EMPLOYEE_PAY = "employee_pay"
    EMPLOYEE_DISMISSAL = "employee_dismissal"
    EMPLOYEE_DEDUCTIONS = "employee_deductions"


def audience_for(topic: Topic) -> Audience:
    return Audience.PATIENT if topic.value.startswith("patient_") else Audience.EMPLOYEE


class EventKind(StrEnum):
    CONTRACT = "CONTRACT"
    SERVICE = "SERVICE"
    PROBLEM_DISCOVERED = "PROBLEM_DISCOVERED"
    DOCUMENT_REQUEST = "DOCUMENT_REQUEST"
    CLAIM_SENT = "CLAIM_SENT"
    CLAIM_RECEIVED = "CLAIM_RECEIVED"
    PAY_DUE = "PAY_DUE"
    PAY_RECEIVED = "PAY_RECEIVED"
    DISMISSAL = "DISMISSAL"
    DISMISSAL_NOTICE_RECEIVED = "DISMISSAL_NOTICE_RECEIVED"


class EventDate(PersonalContract):
    value: date | None = None
    precision: Literal["EXACT", "APPROXIMATE", "UNKNOWN"] = "UNKNOWN"

    @model_validator(mode="after")
    def consistent_date(self) -> "EventDate":
        if (self.precision == "UNKNOWN") != (self.value is None):
            raise ValueError("date precision and value disagree")
        return self


class TimelineEvent(PersonalContract):
    # More than one pay event is allowed; IDs distinguish revisions/events.
    event_id: UUID
    kind: EventKind
    when: EventDate
    confirmed: bool = False


class PersonalIntakeEnvelope(PersonalContract):
    """Future wire shape, currently not accepted by any HTTP write endpoint.

    Authority identifiers and raw document/text fields are deliberately absent.
    An identity/persistence slice and privacy review are required before real use.
    """

    schema_version: Literal["personal-intake.v1"] = "personal-intake.v1"
    audience: Audience
    topic: Topic
    timeline: tuple[TimelineEvent, ...] = Field(default=(), max_length=40)

    @model_validator(mode="after")
    def consistent_scope(self) -> "PersonalIntakeEnvelope":
        if audience_for(self.topic) != self.audience:
            raise ValueError("topic does not belong to audience")
        identifiers = [event.event_id for event in self.timeline]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate timeline event")
        return self


def exact_event_date(event: TimelineEvent) -> date | None:
    """Never fall back to today or another legal event's date."""
    if event.confirmed and event.when.precision == "EXACT":
        return event.when.value
    return None


class PersonalPrincipal(PersonalContract):
    """Server-resolved authority only; never deserialize this from request bodies.

    There is intentionally no resolver or personal identity creation API in M1.
    This is a policy contract, not proof of database/RLS isolation.
    """

    subject_id: UUID
    workspace_id: UUID
    realm: Realm
    active: bool = False


class PersonalCaseScope(PersonalContract):
    case_id: UUID
    owner_subject_id: UUID
    workspace_id: UUID
    audience: Audience


def owner_access_allowed(principal: PersonalPrincipal, case: PersonalCaseScope) -> bool:
    # A clinic OWNER/LEGAL_EDITOR has no shortcut, even for the same human identity.
    return (
        principal.active
        and principal.realm is Realm.PERSONAL
        and principal.workspace_id == case.workspace_id
        and principal.subject_id == case.owner_subject_id
    )


class ReleaseReadiness(PersonalContract):
    """Read-only checklist, NOT environment switches or an authority to launch."""

    identity_and_storage_reviewed: bool = False
    privacy_reviewed: bool = False
    applicable_sources_approved: bool = False
    audience_rules_reviewed: bool = False
    mirrored_cases_passed: bool = False
    human_handoff_reviewed: bool = False
    explicit_launch_approval: bool = False

    def blockers(self) -> tuple[str, ...]:
        return tuple(
            name for name in type(self).model_fields if not getattr(self, name)
        )


def live_analysis_available() -> Literal[False]:
    # A flag/catalog preview must never become a backdoor to real legal answers.
    return False
