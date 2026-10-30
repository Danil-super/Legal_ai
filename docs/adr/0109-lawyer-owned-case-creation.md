# ADR 0109: Active clinic lawyers can create their own cases

Date: 2026-10-29. Status: accepted by the clinic owner for implementation.

## Context

The owner explicitly requested that the active clinic lawyer be able to create
cases for testing AI recommendations. The existing lawyer role opened only the
review workspace; both the Telegram entry and Core intake authorization excluded
it. Promoting a lawyer to administrator would grant unrelated powers.

## Decision

Allow an active `CLINIC_LAWYER` with an active individual clinic entitlement to use
the same narrative-first creation entry and actor-owned drafts as clinic staff.
The lawyer can submit their own case, attach anonymised material, answer follow-up
questions, request analysis and receive the ordinary grounded report and PDF.
These are normal clinic cases, subject to the existing active/monthly quotas,
approved-source checks, risk policy and HIGH/CRITICAL escalation workflow.

The permission does not grant clinic administration or intake/analysis mutations
on other creators' cases. A lawyer may read their own cases, plus cases actually
escalated within their clinic so that the existing review workspace and history
continue to function. Same-clinic membership alone does not grant access to
another creator's un-escalated case or its report. Assigned-lawyer work products
continue through their existing authorization paths.

Revoked or blocked users, expired/missing entitlements, cross-clinic actors and
system-only legal editors remain denied. No role promotions, new tables, risk
changes, automatic legal approvals or unsolicited lawyer messages are involved.
The clinic menu change adds only creation and personal draft buttons; personal
bot entry points, identity and grants remain hidden and unchanged.

## Verification

PostgreSQL tests cover owned v2 intake, material attachment, idempotent submission,
saved report/PDF access, denied foreign-case access/mutation and inactive actors.
The Core/Agent integration scenario also processes a lawyer-created case through
the ordinary approved-evidence analysis job to a saved report. Actual Telegram
dispatcher tests cover the lawyer's visible creation and draft entries. The
existing escalation/work-product and hidden-personal-boundary tests remain gates.

Production release requires successful CI, independent review and deployment
verification. The live role/draft probe uses the existing active lawyer and does
not send Telegram notifications or create a synthetic report in their chat.
