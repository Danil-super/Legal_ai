"""Pure fingerprint contract for frozen case-analysis submissions.

The Legal Core freezes five inputs before external reasoning: the event date, case facts, official
legal evidence, tenant clinic-document context and deterministic risk-policy version. Any change to
any of those inputs invalidates the external result and requires a fresh analysis pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class AnalysisContextFingerprint:
    as_of_date: date
    fact_snapshot_sha256: str
    evidence_trace_sha256: str
    clinic_document_context_trace_sha256: str
    risk_policy_version: str


def analysis_context_is_stale(
    expected: AnalysisContextFingerprint,
    current: AnalysisContextFingerprint,
) -> bool:
    """Return true when any server-authoritative frozen input changed."""

    return expected != current
