# ADR 0035: Scenario search candidates and a non-gold evaluation seed

Date: 2026-09-20
Status: Accepted

## Decision

Extend the deterministic retrieval plan with fixed queries for rework, quality/warranty,
informed consent and medical confidentiality/personal data. Triggers are typed demand
and incident identifiers, never arbitrary model-generated search instructions. New
queries are allowlisted for the existing optional embedding provider; raw service text
remains bounded and local lexical-only. No provider, trusted source or approval gate changes.

Search candidates do not establish a treatment defect, a warranty or an entitlement.
The same approved-only repository, exact-date filter, per-query limit and round-robin
20-fragment default budget remain authoritative. Relevant recall against the real legal
corpus must be measured separately; matching a planned query is not evidence of recall.

Add 40 synthetic domain scenarios in fixtures/evaluation/scenarios.v1.json. Each row
inherits defaults and overrides baseFacts through factsDelta. They exercise query planning,
intake completeness, exact-date selection, deterministic risk/early triage, draft gates and
absence of untrusted text in externally eligible queries. They do not execute Hermes.

The seed is explicitly PENDING_HUMAN_REVIEW and TECHNICAL_REGRESSION_NOT_LEGAL_GOLD. Its
risk expectations snapshot existing code under a synthetic policy; they are not approved
clinical/legal policy and are never imported into runtime policy tables. In particular,
LOW for a confidentiality question or UNKNOWN document inventory is not a safety claim.
A LOW result from the isolated risk function cannot override a failed exact-date gate.
Some internal planner tokens (e.g. DOCUMENT_REQUEST) are not offered by the current wizard.

No legal statements or official citations are fabricated for expected model answers.
Human-reviewed source/version locators and allowed/forbidden propositions are still needed
before measuring legal answer accuracy or changing verifier/risk policy.

## Verification

Run pytest services/legal_core/tests/test_evaluation_seed.py and the existing retrieval,
verifier, risk and integration suites. New fixed search queries must preserve external
privacy and bounded output. The normal runtime remains fail-closed on unavailable evidence.
