# ADR 0060: Report sources come only from verified citations

Date: 2026-10-07
Status: Accepted

## Problem

Retrieval can return more approved, date-applicable fragments than a semantic reviewer accepts
for the final claims. The completed report previously listed every retrieved fragment under
`legalBasis`, so Telegram and PDF could imply that an unused source supported the answer.
This violated the existing distinction between retrieval candidates and verified evidence.

## Decision

The READY report builder receives the final `VerificationDecision`. Its `legalBasis.sources`
contains only fragment IDs in `verified_fragment_ids` of PASSED claims, including both LEGAL
and ACTION claims. It preserves retrieval order for stable Telegram/PDF source numbering.
Model-proposed IDs and uncited retrieved fragments are not sufficient. Every citation must
resolve to a unique retrieved fragment effective on the case date; absent, duplicate, invalid,
or unverified citations fail closed. A published legal conclusion must match a verified claim
and its exact verified citation set.

The complete retrieval trace hash remains unchanged for audit and freshness checks. This
decision changes only the visible source cards of newly created completed reports. It does
not alter approved-only or date-aware retrieval, risk policy, tenant scope, model prompts,
historical report JSON/PDFs, or automatic patient sending. No database migration is needed.

## Verification

Synthetic tests cover an extra retrieved-but-uncited fragment, an ACTION-only verified claim,
stable Telegram citation numbers, canonical report roundtrip, missing/invalid citation gates,
and a legal conclusion lacking a matching verified claim. Full repository quality and
disposable PostgreSQL integration checks remain release gates.
