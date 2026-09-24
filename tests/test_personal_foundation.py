"""M1 contracts are not clinic permissions, legal advice or public intake."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from legal_core.personal.catalog import CATALOG, get_topic, render_demo
from legal_core.personal.contracts import (
    Audience,
    EventDate,
    EventKind,
    PersonalCaseScope,
    PersonalIntakeEnvelope,
    PersonalPrincipal,
    Realm,
    ReleaseReadiness,
    TimelineEvent,
    Topic,
    audience_for,
    exact_event_date,
    live_analysis_available,
    owner_access_allowed,
)
from legal_core.personal.settings import PreviewMode, PreviewSettings, personal_bot_token


def preview_env():
    return {
        "PERSONAL_PREVIEW_MODE": "synthetic", "PERSONAL_PREVIEW_TESTER_IDS": "101,202",
        "PERSONAL_PREVIEW_API_KEY": "preview_" + "p" * 40,
        "PERSONAL_TELEGRAM_BOT_TOKEN": "987654:" + "p" * 40,
        "CLINIC_TELEGRAM_BOT_ID": "123456",
    }


def test_default_is_inert_even_with_unrelated_clinic_configuration():
    config = PreviewSettings.from_mapping({"TELEGRAM_BOT_TOKEN": "never-read-in-off-mode"})
    assert config.mode is PreviewMode.OFF
    assert not config.allows_tester(101)
    assert personal_bot_token({}, config) is None
    assert live_analysis_available() is False
    assert len(ReleaseReadiness().blockers()) == 7


@pytest.mark.parametrize("mode", ["", "1", "true", "True", "SYNTHETIC", "live", "synthetic "])
def test_ambiguous_or_live_modes_fail_closed(mode):
    with pytest.raises(ValueError):
        PreviewSettings.from_mapping({**preview_env(), "PERSONAL_PREVIEW_MODE": mode})


@pytest.mark.parametrize("testers", ["", "0", "-1", "101,101", "101,", "*", "1, 2",
                                     str(2**52), ",".join(str(x) for x in range(1, 34))])
def test_explicit_bounded_tester_allowlist(testers):
    with pytest.raises(ValueError):
        PreviewSettings.from_mapping({**preview_env(), "PERSONAL_PREVIEW_TESTER_IDS": testers})


@pytest.mark.parametrize("secret", ["", "short", "x" * 129, "x" * 32 + "\n"])
def test_preview_credential_validation_does_not_echo_value(secret):
    with pytest.raises(ValueError) as exc:
        PreviewSettings.from_mapping({**preview_env(), "PERSONAL_PREVIEW_API_KEY": secret})
    if secret:
        assert secret not in str(exc.value)


def test_api_credential_not_reused_from_clinic_and_hidden_from_repr():
    env = preview_env()
    config = PreviewSettings.from_mapping(env)
    assert env["PERSONAL_PREVIEW_API_KEY"] not in repr(config)
    with pytest.raises(ValueError):
        PreviewSettings.from_mapping({**env, "AGENT_INTERNAL_KEY": env["PERSONAL_PREVIEW_API_KEY"]})
    assert config.allows_tester(101)
    assert not config.allows_tester(999)
    assert not config.allows_tester(None)
    assert not config.allows_tester(True)


@pytest.mark.parametrize("clinic_id", [None, "", "0", "bad", "987654"])
def test_bot_identity_required_and_cannot_reuse_clinic_bot(clinic_id):
    env = preview_env()
    if clinic_id is None:
        env.pop("CLINIC_TELEGRAM_BOT_ID")
    else:
        env["CLINIC_TELEGRAM_BOT_ID"] = clinic_id
    with pytest.raises(ValueError):
        personal_bot_token(env, PreviewSettings.from_mapping(env))


def test_rotated_token_for_same_bot_is_still_denied():
    env = preview_env()
    env.pop("CLINIC_TELEGRAM_BOT_ID")
    env["TELEGRAM_BOT_TOKEN"] = "987654:" + "q" * 40
    with pytest.raises(ValueError):
        personal_bot_token(env, PreviewSettings.from_mapping(env))
    env["TELEGRAM_BOT_TOKEN"] = "123456:" + "q" * 40
    assert personal_bot_token(env, PreviewSettings.from_mapping(env)) == env[
        "PERSONAL_TELEGRAM_BOT_TOKEN"
    ]


@pytest.mark.parametrize("realm,active,same_owner,same_workspace,expected", [
    (Realm.PERSONAL, True, True, True, True),
    (Realm.CLINIC, True, True, True, False),
    (Realm.PERSONAL, False, True, True, False),
    (Realm.PERSONAL, True, False, True, False),
    (Realm.PERSONAL, True, True, False, False),
])
@pytest.mark.parametrize("audience", list(Audience))
def test_owner_policy_has_no_clinic_or_legal_editor_shortcut(
    realm, active, same_owner, same_workspace, expected, audience,
):
    owner, workspace = uuid4(), uuid4()
    case = PersonalCaseScope(case_id=uuid4(), owner_subject_id=owner,
                             workspace_id=workspace, audience=audience)
    principal = PersonalPrincipal(subject_id=owner if same_owner else uuid4(),
                                  workspace_id=workspace if same_workspace else uuid4(),
                                  realm=realm, active=active)
    assert owner_access_allowed(principal, case) is expected


@pytest.mark.parametrize("key", ["clinic_id", "owner_id", "risk_level", "approved",
                                 "patient_name", "documents", "legal_claims", "subject_id"])
def test_external_intake_cannot_assign_authority_or_raw_data(key):
    with pytest.raises(ValidationError):
        PersonalIntakeEnvelope.model_validate({
            "audience": "PATIENT", "topic": "patient_documents", key: "synthetic",
        })


@pytest.mark.parametrize("topic", list(Topic))
def test_topic_scope_cannot_be_reinterpreted_as_the_other_side(topic):
    audience = audience_for(topic)
    other = Audience.EMPLOYEE if audience is Audience.PATIENT else Audience.PATIENT
    with pytest.raises(ValidationError):
        PersonalIntakeEnvelope(audience=other, topic=topic)
    assert PersonalIntakeEnvelope(audience=audience, topic=topic).audience is audience


@pytest.mark.parametrize("precision,when,confirmed,expected", [
    ("UNKNOWN", None, True, None),
    ("APPROXIMATE", date(2026, 8, 20), True, None),
    ("EXACT", date(2026, 8, 20), False, None),
    ("EXACT", date(2026, 8, 20), True, date(2026, 8, 20)),
])
def test_timeline_unknown_or_unconfirmed_does_not_become_exact(
    precision, when, confirmed, expected,
):
    event = TimelineEvent(event_id=uuid4(), kind=EventKind.SERVICE,
                          when=EventDate(value=when, precision=precision), confirmed=confirmed)
    assert exact_event_date(event) == expected


@pytest.mark.parametrize("value,precision", [(None, "EXACT"), (None, "APPROXIMATE"),
                                              (date(2026, 9, 24), "UNKNOWN")])
def test_date_precision_must_match_value(value, precision):
    with pytest.raises(ValidationError):
        EventDate(value=value, precision=precision)


def test_distinct_events_keep_distinct_dates_and_duplicate_ids_are_rejected():
    first = TimelineEvent(event_id=uuid4(), kind=EventKind.PAY_DUE,
                          when=EventDate(value=date(2026, 9, 1), precision="EXACT"),
                          confirmed=True)
    second = first.model_copy(update={"event_id": uuid4(), "when": EventDate()})
    intake = PersonalIntakeEnvelope(audience=Audience.EMPLOYEE, topic=Topic.EMPLOYEE_PAY,
                                    timeline=(first, second))
    assert exact_event_date(intake.timeline[1]) is None
    with pytest.raises(ValidationError):
        PersonalIntakeEnvelope(audience=Audience.EMPLOYEE, topic=Topic.EMPLOYEE_PAY,
                               timeline=(first, first))


def test_readiness_checklist_is_not_a_switch_to_live_analysis():
    ready = ReleaseReadiness(**{name: True for name in ReleaseReadiness.model_fields})
    assert ready.blockers() == ()
    assert live_analysis_available() is False


def test_catalog_is_six_fixed_examples_without_legal_conclusions():
    assert len(CATALOG) == len(Topic) == 6
    assert len({card.topic for card in CATALOG}) == 6
    for card in CATALOG:
        text = render_demo(card)
        assert len(text) < 3000
        assert "NOT_AVAILABLE" in text
        assert "юридический" in text.lower()
        assert get_topic(card.topic.value) == card
        assert card.source_candidates and len(card.fact_questions) == 4
    assert get_topic("unknown") is None
