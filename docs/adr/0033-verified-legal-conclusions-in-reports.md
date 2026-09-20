# ADR 0033: Publish verified legal conclusions in canonical reports

Date: 2026-09-20
Status: Accepted

## Problem

Researcher LEGAL claims pass semantic and structural verification, but the report builder previously
received only verified ACTION claims. Users saw risk, actions and a source list without the checked
legal assessment. This is a confirmed output-path defect, not proof that a deployed analysis stack is
running: disabled services, missing approvals and evidence failures remain separate failure modes.

## Decision

Add the optional `legalConclusions` array to `dental-case-report.v1`. Each item contains `claimId`,
unchanged `text`, server-owned `verificationStatus: VERIFIED`, `evidenceFragmentIds` and
`requiredFactKeys`. Select items from the final Legal Core verification decision, not from an LLM flag.
Only LEGAL claims are included; ACTION claims remain recommendations. Cite the verifier's actual
`verified_fragment_ids`, not all IDs proposed by the researcher.

The report contract rejects duplicate claims, absent/foreign references, out-of-date sources and
conclusions in blocked analyses or analyses with unavailable risk. The all-or-nothing verification
policy, approved-source gates, exact-date selection, tenant access and risk/escalation rules do not
change. Unverified model recommendations and patient drafts remain unpublished.

Telegram shows a separate legal-assessment section and all numbered sources needed by its citations.
Existing UTF-16-aware message splitting retains complete conclusions, including trailing qualifiers.
PDF renders the same canonical texts and source numbering with escaped markup. The minimal lawyer
handoff remains free of case narrative and conclusion text; authorized full reports/PDFs are separate.

## Compatibility and release

Missing `legalConclusions` defaults to an empty list. No database table or column changes are needed;
reports are already stored as JSON. Do not rewrite historical report JSON, hashes or PDF bytes. Old
reports do not acquire conclusions retroactively. Deploy Legal Core and gateway from the same commit:
older strict-contract consumers may reject the new additive field during mixed-version operation.

The first questionnaire PDF is still an intake card, not the completed legal analysis. This change
does not enable Hermes, approve a corpus/risk policy or authorize patient sending. A new completed
analysis is required to see newly persisted conclusions. An ACTION-only result explicitly says that
no separate legal conclusions were saved; the renderer must never invent them.

## Verification

`tests/test_legal_conclusions.py` covers server selection, verified citation subsets, rejection gates,
legacy reports, risk/draft invariants, escaped and deterministic PDF rendering, full Telegram delivery,
minimal-handoff isolation and the real verifier-to-report domain pipeline. Run the complete repository
quality and PostgreSQL integration jobs in CI in addition to the focused tests.
