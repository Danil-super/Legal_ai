# Specification: factual clarification after analysis abstention

## Status and objective

Approved by the owner in chat on 2026-10-14; the owner subsequently approved the
implementation plan and FC1–FC11 tasks. FC1–FC10 implementation and local vertical
integration and reviewed CI/release are complete (PR95, main fea949f).
The live legal-readiness gate remains open: both entry paths persisted cases but
the unchanged Hermes/provider path failed with ANALYSIS_PROVIDER_INVALID.
This is one vertical
capability within the existing `case-core`, `agent-orchestration` and
`telegram-gateway` modules of CAPABILITY_MAP.md: make a blocked analysis actionable
without asking the clinic to perform legal qualification.

Approved boundaries: the existing providers/models remain unchanged; only
de-identified factual context may be processed; historical case/report snapshots
remain immutable. Approval does not approve law or historical examples.

The real fictional smoke returned no claims and no concrete report missingFacts.
A diagnostic model response identified unknown paid nature/service date. A
multi-factor fictional contrast produced two SUPPORTED reviews, but no Core
submission. This supports further investigation, not a guarantee that these
questions alone fix every case or prove legal correctness.

## User flow and bounded contract

1. Both quick-text and guided paths use the same clarification presentation and
   durable state. Keep the initial ordinary-language intake; do not add an
   unconditional questionnaire to unrelated cases.
2. The research response may propose up to three structured factual clarification
   requests, separately from legal claims. It cannot supply arbitrary question
   wording, commands, URLs or a new legal classification. Legal Core accepts only
   allowlisted question IDs appropriate to the case, unknown answer and service.
3. First allowlist: whether the relevant service was paid/publicly covered/other/
   unknown; when that service was provided (exact/approximate/unknown); what the
   clinic needs to resolve (receive/explain the complaint, answer a request,
   another factual purpose, unknown). Wording is fixed and neutral, not a prompt
   asking whether a statutory claim, defect, harm or deadline legally exists.
   No bank details, payer identity, receipt bytes or new medical details collected.
4. For several affected services, bind each service question to a bounded existing
   service slot; do not silently assign one date/payment answer to all services.
   At most five services, as in the current intake. Unknown and mixed situations
   remain explicit. Core validates shape and scope; the gateway cannot set tenant.
5. Core shows validated questions in the blocked report and Telegram. An empty
   claim batch remains BLOCKED; unverified recommendations/drafts are discarded.
   Unsupported/out-of-scope requests fail closed. With no valid clarification,
   explain the evidence limitation and offer the existing authorised human path;
   do not invent a reason or claim every abstention is caused by missing facts.
6. Persist only user-confirmed answers in the existing protected intake/fact
   lifecycle. Never extract them as truth from model suggestions. Back/cancel,
   restart/resume, stale messages, optimistic revisions and idempotent retry work
   as for the existing wizard. No unlimited automatic model retry loop.
7. A finalized case is not reopened. The existing creator-only clarification
   preview/new-draft recovery may prefill the factual answers; the user sees that
   the old case/report is unchanged and confirms the new intake. Original files
   are not copied or sent to the model. Existing retention periods apply.

## Legal and temporal boundaries

EVENT_DATE is not a service date or receipt date. Neither a payment answer nor
a service date automatically establishes a legal duty, defect, deadline or
liability. A service outside the approved evidence's applicability interval
cannot be justified by citing a copy applicable only to the later event date.
The existing single retrieval anchor is not silently converted to a multi-date
legal resolver; unsupported temporal conclusions remain blocked/human-reviewed.

All legal/action claims still require approved date-applicable retrieved evidence,
semantic review and Legal Core verification. HIGH/CRITICAL early escalation,
policy v4 and the 10,000 RUB threshold are unchanged and must not wait for these
questions. Nothing is automatically sent to a patient.

## Structure, style and implementation constraints

Contracts/factual validation live in services/legal_core/src/legal_core; research
contracts and projection in services/agent_orchestrator/src/agent_orchestrator;
fixed Russian questions and durable handlers in
services/gateway/telegram/src/telegram_gateway. Tests live beside each service.
Use existing strict extra-forbid Pydantic contracts, typed enums, camelCase wire
aliases, server-generated scope and bounded lists. An illustrative request is
`{"questionId":"SERVICE_PAYMENT_BASIS","serviceSlot":0}`; no model-authored
question text or inferred answer is accepted.

No new dependency, provider, role, upload handler, retention rule or policy.
Prefer the existing fact/draft storage; any required database constraint change
needs Alembic tests/migration. A new table would require separate agreement.
The critical contract requires an ADR and tests in the same implementation.

## Commands and testing strategy

With the project venv and service PYTHONPATH:

```bash
python -m pytest -q --tb=short
python -m ruff check .
python -m mypy services/legal_core/src services/gateway/telegram/src services/agent_orchestrator/src
docker compose config --quiet
```

Full regression uses disposable PostgreSQL and MinIO, not production patient
data. Acceptance tests must cover quick/manual parity; missing versus confirmed
answers; mixed/multiple services; unknown/approximate/earlier dates; malformed or
injected model requests; tenant/creator denial; expiry; stale buttons; lost replies;
restart/resume; repeat confirmation; retained historical hashes; safe empty claims;
and unchanged urgent routing. Fixed fictional cases only.

A live release gate requires a real Core-persisted evidence-linked positive
answer and truthful clarification/negative results. Process-local model SUPPORTED
or mocked model tests alone do not meet it. Report failures without weakening
assertions, evidence gates or model settings.

## Non-goals and implementation gate

This slice does not implement online reference-case context, automatically approve
the 67 DRAFT examples, change the treatment-query allowlist, or certify full legal
readiness. The independently reproduced treatment-search proposal remains separate.

The owner approved this factual-clarification contract, focused plan and tasks.
That approval is not a production-release approval. Online
reference context will require its own approved curated-card/data-flow contract:
de-identified reviewed hints only, never original historical files or legal evidence.
