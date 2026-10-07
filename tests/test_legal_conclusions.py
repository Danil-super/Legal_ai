# ruff: noqa: RUF001
"""Synthetic regressions for verified conclusions from server selection to delivery."""

from datetime import UTC, date, datetime
from html import escape
from types import SimpleNamespace
from uuid import UUID

import pytest
from legal_core.contracts import CanonicalReport, CaseStatus, FactKey, LegalConclusion
from legal_core.legal_conclusions import select_verified_legal_conclusions
from legal_core.reports import build_analysis_report, build_intake_report, render_report_pdf
from legal_core.risk_engine import RiskAssessment, RiskLevel
from legal_core.verifier import VerificationDecision, VerificationResult, VerifiedClaim
from telegram_gateway.legal_conclusion_display import legal_conclusion_lines

FRAGMENT = UUID(int=1)
EXTRA_FRAGMENT = UUID(int=2)
CASE_DATE = date(2026, 9, 1)
GENERATED = datetime(2026, 9, 20, tzinfo=UTC)
CLAIM_TEXT = "Синтетический проверенный вывод; применимость ограничена фактами кейса."


def _fragment(fragment_id=FRAGMENT):
    return SimpleNamespace(
        fragment_id=fragment_id, document_id=UUID(int=10), version_id=UUID(int=11),
        article="1", part=None, point=None, structural_path="Тестовый пункт 1",
        document_title="Синтетический источник для теста, не закон", official_number="TEST",
        fragment_text="Синтетический текст доказательства.", issuer="TEST",
        effective_from=date(2026, 1, 1), effective_to=None,
        publication_date=date(2026, 1, 1), version_date=date(2026, 1, 1),
        source_url="https://example.invalid/test-source", text_sha256="a" * 64,
        raw_sha256="b" * 64,
    )


def _conclusion(**changes):
    data = dict(claimId="legal-1", text=CLAIM_TEXT, evidenceFragmentIds=[FRAGMENT],
                requiredFactKeys=[FactKey.FORMAL_CLAIM])
    data.update(changes)
    return LegalConclusion(**data)


def _report(level=RiskLevel.LOW, conclusions=None, evidence=None, verification=None):
    conclusions = [_conclusion()] if conclusions is None else conclusions
    cited_id = conclusions[0].evidence_fragment_ids[0]
    if verification is None:
        verification = VerificationDecision(claims=(
            VerifiedClaim("action-1", VerificationResult.VERIFIED, None, (cited_id,)),
            *(VerifiedClaim(item.claim_id, VerificationResult.VERIFIED, None,
                             tuple(item.evidence_fragment_ids)) for item in conclusions),
        ))
    return build_analysis_report(
        report_id=UUID(int=20), analysis_run_id=UUID(int=21), case_id=UUID(int=22),
        public_number="SYNTHETIC-22", case_status=(
            CaseStatus.ESCALATION_REQUIRED if level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            else CaseStatus.REPORT_READY
        ), report_version=2, generated_at=GENERATED, as_of_date=CASE_DATE,
        facts={FactKey.PROBLEM_SUMMARY: "Синтетическое описание без данных пациента.",
               FactKey.FORMAL_CLAIM: "NO"}, missing_facts=[],
        risk=RiskAssessment(level, ("TEST_REASON",), "test.v1", "c" * 64,
                            level is RiskLevel.LOW),
        evidence_trace_sha256="d" * 64,
        evidence=evidence if evidence is not None else [_fragment()],
        verification=verification,
        clinic_document_context_trace_sha256="e" * 64, clinic_document_context=[],
        verified_action_items=["Синтетическое проверенное действие."],
        verified_legal_conclusions=conclusions,
    )


def _claim(kind="LEGAL", claim_id="legal-1"):
    return SimpleNamespace(
        claim_id=claim_id, kind=kind, text=CLAIM_TEXT,
        evidence_fragment_ids=(FRAGMENT, EXTRA_FRAGMENT),
        required_fact_keys=(FactKey.FORMAL_CLAIM,),
    )


def _decision(*rows, allowed=True):
    return SimpleNamespace(analysis_allowed=allowed, claims=rows)


def _checked(claim_id="legal-1", result="VERIFIED", refs=(FRAGMENT,)):
    return SimpleNamespace(claim_id=claim_id, result=result, verified_fragment_ids=refs)


def test_selector_preserves_exact_text_and_only_reviewed_source_subset():
    output = select_verified_legal_conclusions([_claim()], _decision(_checked()))
    assert output[0].text == CLAIM_TEXT
    assert output[0].evidence_fragment_ids == [FRAGMENT]
    assert output[0].required_fact_keys == [FactKey.FORMAL_CLAIM]
    assert output[0].verification_status == "VERIFIED"


def test_action_claims_are_not_legal_conclusions():
    assert select_verified_legal_conclusions(
        [_claim("ACTION")], _decision(_checked())
    ) == []


@pytest.mark.parametrize("result", ["UNSUPPORTED", "CONTRADICTED", "INSUFFICIENT_FACTS"])
def test_all_or_nothing_gate_does_not_publish_a_verified_subset(result):
    claims = [_claim(), _claim(claim_id="legal-2")]
    verification = _decision(_checked(), _checked("legal-2", result), allowed=False)
    assert select_verified_legal_conclusions(claims, verification) == []


def test_abstention_does_not_produce_conclusions():
    assert select_verified_legal_conclusions([], _decision(allowed=False)) == []


@pytest.mark.parametrize("verification", [
    _decision(_checked("other")),
    _decision(_checked(), _checked()),
    _decision(_checked(result="UNSUPPORTED")),
    _decision(_checked(refs=(UUID(int=999),))),
    _decision(_checked(refs=())),
])
def test_selector_rejects_inconsistent_verifier_records(verification):
    with pytest.raises(ValueError):
        select_verified_legal_conclusions([_claim()], verification)


@pytest.mark.parametrize("changes", [
    {"text": " "}, {"claimId": " "}, {"text": "x" * 4_001},
    {"verificationStatus": "SUPPORTED"}, {"evidenceFragmentIds": []},
    {"evidenceFragmentIds": [FRAGMENT, FRAGMENT]},
    {"requiredFactKeys": [FactKey.FORMAL_CLAIM, FactKey.FORMAL_CLAIM]},
])
def test_conclusion_contract_rejects_invalid_data(changes):
    with pytest.raises(ValueError):
        _conclusion(**changes)


def test_legacy_report_defaults_to_empty_conclusions_without_rewriting_history():
    legacy = _report().model_dump(mode="json", by_alias=True)
    del legacy["legalConclusions"]
    restored = CanonicalReport.model_validate(legacy)
    assert restored.legal_conclusions == []
    assert legal_conclusion_lines(legacy) == []
    assert "legalConclusions" not in legacy


def test_report_roundtrip_keeps_conclusions_and_source_ids():
    payload = _report().model_dump(mode="json", by_alias=True)
    assert payload["legalConclusions"][0]["evidenceFragmentIds"] == [str(FRAGMENT)]
    assert CanonicalReport.model_validate(payload).legal_conclusions == [_conclusion()]


@pytest.mark.parametrize("verification", [
    VerificationDecision(claims=()),
    VerificationDecision(claims=(
        VerifiedClaim("action-1", VerificationResult.UNSUPPORTED, None, (FRAGMENT,)),
    )),
    VerificationDecision(claims=(
        VerifiedClaim("action-1", VerificationResult.VERIFIED, None, ()),
    )),
    VerificationDecision(claims=(
        VerifiedClaim("action-1", VerificationResult.VERIFIED, None, (UUID(int=999),)),
    )),
])
def test_ready_report_fails_closed_without_valid_verified_citations(verification):
    with pytest.raises(ValueError):
        _report(verification=verification)


def test_ready_report_rejects_conclusion_without_matching_verified_claim():
    verification = VerificationDecision(claims=(
        VerifiedClaim("action-1", VerificationResult.VERIFIED, None, (FRAGMENT,)),
    ))
    with pytest.raises(ValueError, match="matching verified claim"):
        _report(verification=verification)


@pytest.mark.parametrize("fault", ["unknown_source", "expired", "future", "duplicate_claim",
                                  "failed_verifier", "unavailable_risk", "duplicate_source"])
def test_report_rejects_unpublishable_conclusions(fault):
    payload = _report().model_dump(mode="json", by_alias=True)
    if fault == "unknown_source":
        payload["legalConclusions"][0]["evidenceFragmentIds"] = [str(EXTRA_FRAGMENT)]
    elif fault == "expired":
        payload["legalBasis"]["sources"][0]["effectiveTo"] = CASE_DATE.isoformat()
    elif fault == "future":
        payload["legalBasis"]["sources"][0]["effectiveFrom"] = "2027-01-01"
    elif fault == "duplicate_claim":
        payload["legalConclusions"] *= 2
    elif fault == "failed_verifier":
        payload["analysis"]["verifierStatus"] = "BLOCKED"
    elif fault == "unavailable_risk":
        payload["risk"]["level"] = "UNAVAILABLE"
    else:
        payload["legalBasis"]["sources"] *= 2
    with pytest.raises(ValueError):
        CanonicalReport.model_validate(payload)
    with pytest.raises(ValueError):
        legal_conclusion_lines(payload)


def test_blocked_report_cannot_carry_legal_conclusions():
    report = build_intake_report(
        report_id=UUID(int=20), case_id=UUID(int=22), public_number="SYNTHETIC-22",
        case_status=CaseStatus.ANALYSIS_BLOCKED, report_version=1,
        generated_at=GENERATED, facts={}, missing_facts=[],
    )
    assert report.legal_conclusions == []
    payload = report.model_dump(mode="json", by_alias=True)
    payload["legalConclusions"] = [_conclusion().model_dump(mode="json", by_alias=True)]
    with pytest.raises(ValueError, match="passed analysis"):
        CanonicalReport.model_validate(payload)


@pytest.mark.parametrize("level", [RiskLevel.LOW, RiskLevel.MEDIUM,
                                   RiskLevel.HIGH, RiskLevel.CRITICAL])
def test_conclusions_do_not_weaken_risk_escalation_or_patient_draft_policy(level):
    report = _report(level)
    assert report.legal_conclusions
    assert report.risk.escalation_required == (level in {RiskLevel.HIGH, RiskLevel.CRITICAL})
    assert (report.draft_response.status == "AVAILABLE") == (level is RiskLevel.LOW)
    assert report.draft_response.human_approval_required


def test_display_keeps_full_text_and_does_not_silently_drop_invalid_new_data():
    text = "Тест 🦷 " * 450 + "ВАЖНАЯ ОГОВОРКА В КОНЦЕ"
    report = _report(conclusions=[_conclusion(text=text)])
    displayed = "\n".join(legal_conclusion_lines(report.model_dump(mode="json", by_alias=True)))
    assert text in displayed
    assert "Основание: [1]." in displayed
    with pytest.raises(ValueError):
        legal_conclusion_lines({"legalConclusions": None})


def test_pdf_includes_escaped_conclusions_and_matching_source_numbers(monkeypatch):
    from legal_core import reports

    paragraphs = []
    original = reports.Paragraph

    def capture(text, *args, **kwargs):
        paragraphs.append(text)
        return original(text, *args, **kwargs)

    monkeypatch.setattr(reports, "Paragraph", capture)
    text = "Синтетический <вывод> & уточнение."
    unused = _fragment(EXTRA_FRAGMENT)
    unused.document_title = "Непроцитированный источник"
    report = _report(conclusions=[_conclusion(text=text)], evidence=[unused, _fragment()])
    pdf = render_report_pdf(report)
    assert pdf.startswith(b"%PDF-")
    assert pdf == render_report_pdf(report)
    assert "Юридическая оценка" in paragraphs
    assert f"1. {escape(text)}" in paragraphs
    assert "Основание: [1]." in paragraphs
    assert any(p.startswith("[1] Синтетический источник") for p in paragraphs)
    assert not any("Непроцитированный источник" in p for p in paragraphs)


def test_telegram_runtime_delivers_all_conclusions_and_sources_without_truncation():
    from telegram_gateway.analysis_runtime import telegram_analysis_messages

    conclusions = [_conclusion(claimId=f"c{i}", text=("Тест 🦷 " * 450) + f" КОНЕЦ-{i}",
                               evidenceFragmentIds=[EXTRA_FRAGMENT]) for i in range(3)]
    evidence = [_fragment(UUID(int=i)) for i in range(10, 17)] + [_fragment(EXTRA_FRAGMENT)]
    report = _report(conclusions=conclusions, evidence=evidence)
    assert [source.fragment_id for source in report.legal_basis.sources] == [EXTRA_FRAGMENT]
    payload = {"analysisAllowed": True, "riskLevel": "LOW", "escalationRequired": False,
               "report": {"reportJson": report.model_dump(mode="json", by_alias=True)}}
    messages = telegram_analysis_messages(payload)
    text = "".join(messages)
    assert len(messages) > 1
    assert all(len(m.encode("utf-16-le")) // 2 <= 4_000 for m in messages)
    for conclusion in conclusions:
        assert conclusion.text in text
    assert "Основание: [1]." in text
    assert "[1] Синтетический источник" in text
    assert "Автоматическая отправка пациенту отключена." in text


def test_telegram_runtime_does_not_publish_conclusions_for_blocked_analysis_or_handoff():
    from telegram_gateway.analysis_runtime import (
        telegram_analysis_messages,
        telegram_lawyer_handoff_summary,
    )

    payload = {"analysisAllowed": False, "riskLevel": "UNAVAILABLE",
               "report": {"reportJson": _report().model_dump(mode="json", by_alias=True)}}
    assert CLAIM_TEXT not in "".join(telegram_analysis_messages(payload))
    payload.update(analysisAllowed=True, riskLevel="HIGH", escalationRequired=True)
    payload["report"]["reportJson"] = _report(RiskLevel.HIGH).model_dump(mode="json", by_alias=True)
    assert CLAIM_TEXT not in telegram_lawyer_handoff_summary(payload)


def test_real_verifier_pipeline_keeps_only_checked_legal_claims():
    from legal_core.analysis import analyze_frozen_case
    from legal_core.risk_engine import RiskPolicy
    from legal_core.verifier import (
        ClaimKind,
        ProposedClaim,
        SemanticReview,
        SemanticVerdict,
    )

    facts = {key: "NO" for key in (FactKey.FORMAL_CLAIM, FactKey.HARM_CLAIMED,
                                   FactKey.LAWYER_CONTACT, FactKey.REGULATOR_OR_COURT,
                                   FactKey.REGULATOR_THREAT)}
    claims = [ProposedClaim("legal-1", ClaimKind.LEGAL, CLAIM_TEXT, (FRAGMENT,)),
              ProposedClaim("action-1", ClaimKind.ACTION, "Тестовое действие.", (FRAGMENT,))]
    reviews = [SemanticReview(c.claim_id, SemanticVerdict.SUPPORTED, (FRAGMENT,)) for c in claims]
    outcome = analyze_frozen_case(
        facts=facts, as_of_date=CASE_DATE, evidence=[_fragment()], claims=claims,
        semantic_reviews=reviews, risk_policy=RiskPolicy("test.v1", 100_000),
    )
    selected = select_verified_legal_conclusions(claims, outcome.verification)
    assert outcome.analysis_allowed
    assert [c.claim_id for c in selected] == ["legal-1"]
    assert _report(conclusions=selected).legal_conclusions[0].text == CLAIM_TEXT
    reviews[0] = SemanticReview("legal-1", SemanticVerdict.UNSUPPORTED, (FRAGMENT,))
    blocked = analyze_frozen_case(
        facts=facts, as_of_date=CASE_DATE, evidence=[_fragment()], claims=claims,
        semantic_reviews=reviews, risk_policy=RiskPolicy("test.v1", 100_000),
    )
    assert not blocked.analysis_allowed
    assert select_verified_legal_conclusions(claims, blocked.verification) == []
