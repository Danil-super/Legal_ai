from legal_core.contracts import FactKey
from legal_core.risk_engine import RiskAssessment, RiskLevel
from legal_core.safe_patient_draft import (
    SAFE_OPERATIONAL_DRAFT_VERSION,
    build_safe_patient_draft,
)


def _risk(
    level: RiskLevel,
    *,
    external_draft_allowed: bool,
) -> RiskAssessment:
    return RiskAssessment(
        level=level,
        reason_codes=("SYNTHETIC_TEST",),
        policy_version="dental-risk.v1",
        fact_snapshot_sha256="a" * 64,
        external_draft_allowed=external_draft_allowed,
    )


def test_low_risk_draft_is_generic_and_does_not_echo_free_text() -> None:
    sensitive_summary = "Пациент Иван Иванов, телефон +7 999 123-45-67, сообщил о боли."

    draft = build_safe_patient_draft(
        {FactKey.PROBLEM_SUMMARY: sensitive_summary},
        _risk(RiskLevel.LOW, external_draft_allowed=True),
    )

    assert draft.status == "AVAILABLE"
    assert draft.text is not None
    assert draft.policy_version == SAFE_OPERATIONAL_DRAFT_VERSION
    assert "Иван Иванов" not in draft.text
    assert "+7 999" not in draft.text
    assert sensitive_summary not in draft.text
    assert "не будем делать выводы о причинах" in draft.text
    assert draft.human_approval_required is True


def test_high_and_critical_risk_drafts_require_human_legal_review() -> None:
    for level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        draft = build_safe_patient_draft(
            {},
            _risk(level, external_draft_allowed=False),
        )

        assert draft.status == "BLOCKED"
        assert draft.reason_code == "HUMAN_LEGAL_REVIEW_REQUIRED"
        assert draft.policy_version == SAFE_OPERATIONAL_DRAFT_VERSION
        assert draft.text is None


def test_defense_in_depth_blocks_formal_claim_even_if_risk_configuration_drifts() -> None:
    draft = build_safe_patient_draft(
        {FactKey.FORMAL_CLAIM: {"boolean": True}},
        _risk(RiskLevel.LOW, external_draft_allowed=True),
    )

    assert draft.status == "BLOCKED"
    assert draft.reason_code == "PATIENT_DRAFT_SAFETY_SIGNAL"
    assert draft.policy_version == SAFE_OPERATIONAL_DRAFT_VERSION


def test_unavailable_or_policy_blocked_draft_fails_closed() -> None:
    unavailable = build_safe_patient_draft(
        {},
        _risk(RiskLevel.UNAVAILABLE, external_draft_allowed=False),
    )
    policy_blocked = build_safe_patient_draft(
        {},
        _risk(RiskLevel.MEDIUM, external_draft_allowed=False),
    )

    assert unavailable.status == "BLOCKED"
    assert policy_blocked.status == "BLOCKED"
    assert unavailable.reason_code == "RISK_POLICY_BLOCKS_EXTERNAL_DRAFT"
    assert policy_blocked.reason_code == "RISK_POLICY_BLOCKS_EXTERNAL_DRAFT"
    assert unavailable.policy_version == SAFE_OPERATIONAL_DRAFT_VERSION
    assert policy_blocked.policy_version == SAFE_OPERATIONAL_DRAFT_VERSION
