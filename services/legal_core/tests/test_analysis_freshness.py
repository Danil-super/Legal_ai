from dataclasses import replace
from datetime import date

import pytest
from legal_core.analysis_freshness import (
    AnalysisContextFingerprint,
    analysis_context_is_stale,
)


BASE = AnalysisContextFingerprint(
    as_of_date=date(2026, 9, 1),
    fact_snapshot_sha256="a" * 64,
    evidence_trace_sha256="b" * 64,
    clinic_document_context_trace_sha256="c" * 64,
    risk_policy_version="risk-policy.v1",
)


def test_identical_analysis_context_is_not_stale() -> None:
    assert analysis_context_is_stale(BASE, BASE) is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("as_of_date", date(2026, 8, 31)),
        ("fact_snapshot_sha256", "d" * 64),
        ("evidence_trace_sha256", "e" * 64),
        ("clinic_document_context_trace_sha256", "f" * 64),
        ("risk_policy_version", "risk-policy.v2"),
    ],
)
def test_any_frozen_input_change_makes_analysis_context_stale(
    field: str,
    value: object,
) -> None:
    changed = replace(BASE, **{field: value})

    assert analysis_context_is_stale(BASE, changed) is True


def test_clinic_document_change_alone_invalidates_external_reasoning() -> None:
    changed = replace(BASE, clinic_document_context_trace_sha256="0" * 64)

    assert BASE.fact_snapshot_sha256 == changed.fact_snapshot_sha256
    assert BASE.evidence_trace_sha256 == changed.evidence_trace_sha256
    assert BASE.risk_policy_version == changed.risk_policy_version
    assert analysis_context_is_stale(BASE, changed) is True
