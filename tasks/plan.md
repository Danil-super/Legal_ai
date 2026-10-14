# Implementation Plan: Dental Legal AI MVP

## Factual analysis clarification — contract approved 2026-10-14

Status: plan and FC1–FC11 tasks approved by the owner on 2026-10-14. This extends the
ongoing case-readiness work; all pre-existing incomplete plans/tasks below remain.
Contract: SPEC-analysis-factual-clarifications.md. No application code or
production configuration is changed by this planning step.

### Delivery order

Use the existing modules and storage. Dependency order:
strict shared contracts → protected draft/fact validation → model/Core request
transport → persisted blocked questions → durable user answers → creator-only
recovery → temporal verification → shared report presentation → end-to-end gates.

1. **FC1–FC4: make a blocked analysis actionable.** Define bounded request IDs and
   service slots, retaining strict extra-forbid contracts. Add optional empty
   lists for old reports/model responses; introduce a typed clarification-answer
   JSON fact and draft field, never a guessed legacy claim/date. Model requests
   travel separately from claims through the existing internal submission API.
   Core validates them against its frozen case facts, not the supplied tenant,
   and stores only accepted IDs/slots in its canonical blocked report. Reject
   invalid/duplicate/out-of-scope requests. Unverified recommendations/drafts
   remain discarded. No request alone produces a legal conclusion or READY.
2. **FC5–FC8: answer the questions through the existing wizard.** Fixed neutral
   Russian questions collect only the approved business/date/purpose facts.
   Bind service answers to the unchanged affected-service slot set; reset them
   when that set changes. Both entry paths share parsing, durable saves, summary
   and explicit confirmation. A completed case is immutable: use the existing
   creator-only new-draft recovery, now bound to the displayed source report and
   facts. Preserve file ownership and do not copy originals. UNKNOWN is allowed,
   but recorded as attempted so it does not trigger an automatic repeated loop.
3. **FC9–FC11: prove safety, present results and prepare release.** Check relevant
   service dates without changing the current retrieval anchor or policy. Unknown,
   approximate or unsupported historical applicability cannot pass as known
   merely because EVENT_DATE is exact. Shared CanonicalReport presentation shows
   the concrete questions in Telegram/PDF. Verify both complete entry paths,
   positive and negative results, urgent routing, isolation and recovery on
   disposable PostgreSQL/MinIO, then reviewed CI/release and a real Core smoke.

### Storage, compatibility and scope decisions

- Reuse CaseFact JSON, TelegramIntakeDraft JSON and immutable CaseReport JSON.
  Existing clinic_id/RLS, actor checks, revisions, idempotency and 30/90-day
  retention continue. No new table or data backfill is planned. If an existing
  database constraint needs an additive change, require Alembic tests; a new
  table or new sensitive data category requires separate agreement.
- SERVICE_PAYMENT_BASIS and SERVICE_PROVIDED_DATE questions require a valid
  service slot; RESPONSE_PURPOSE is case-level. At most three requests per model
  batch; at most five affected services. Core chooses fixed question wording.
  Exact answer storage shape is tested in FC1/FC2, not an unrestricted JSON bag.
- Old readers are strict: `_domain_facts` converts stored keys through FactKey,
  and CanonicalReport forbids unknown fields. Do not call writes rollback-safe
  merely because no table changed. Establish and test a reader-compatible
  revision before emitting new persisted fields; record that revision as the
  minimum rollback target. After new writes, f4eaee8 alone is not a proven safe
  rollback. No ignoring unknown facts or weakening schema validators to mask it.
- A valid unanswered clarification keeps the result blocked; confirmed urgent
  facts still use the existing early HIGH/CRITICAL route immediately. The
  payment answer does not change risk policy or the 10,000 RUB threshold.
- Keep the single EVENT_DATE retrieval anchor. A known service date does not
  make a later LAWYER_CURRENT_COPY applicable to an earlier service. For a
  service-dependent claim, require validated temporal support from the cited
  fragments; if dependency/date binding cannot be established, block that legal
  analysis for clarification/human review rather than infer applicability.
- Do not automatically rerun a finalized case, publish model advice, infer
  answers from a narrative, send raw attachments to models, approve norms or
  references, change providers/models, widen roles or edit personal features.
  Treatment-query expansion and online curated reference context remain separate.

### Checkpoints, collaboration and release

FC1–FC4, FC5–FC8 and FC9–FC11 each have a regression checkpoint. Shared contracts,
state changes and database work are sequential. During implementation, isolated
branches may be used for independent RED tests and scoped reviews; no parallel
writers on shared contracts. No agent run/completion is claimed by this plan.

The full release gate uses the existing project quality/security/container and
GitHub workflows, without skipped tests or lower thresholds. Add bounded existing-
style audit events for accepted clarification IDs/counts/status only: no answers,
case narrative or provider body in telemetry. A real positive Core-persisted report
with cited approved norms is required; process-local SUPPORTED is insufficient.
Live probes use fictional cases and the owner channel, not notifications to real
lawyers unless separately authorised. Actual release/activation needs reviewed
immutable-head approval; this plan is not a deployment authorisation.

Plan and FC1–FC11 task approval received; reviewed release remains separate. If compatibility
or storage cannot satisfy the approved boundaries, report the exact blocker before
changing the contract. Successful technical tests still do not certify legal
quality of all 67 unreviewed historical cases or unrestricted commercial readiness.

## Authorized immediate lawyer alerts — 2026-10-07

Implement `SPEC-lawyer-notifications.md` in a separate branch: privacy-minimal
Telegram delivery → transactional outbox and leased internal API → disposable
PostgreSQL/authorization/retry proofs → independent review and release.
Every active CLINIC_LAWYER in the same clinic is notified until assignment;
the current subscription and actor-resolution constraints still apply.

## Overview

Реализация следует ТЗ v0.1 и карте `CAPABILITY_MAP.md`. Работа идёт вертикальными срезами: каждый срез оставляет систему запускаемой и проверяемой. LLM/Telegram не подключаются, пока Legal Core, tenant isolation и детерминированные safety-контракты не доказаны тестами.

## Architecture Decisions

### Complete attorney package preparation — approved plan, 2026-09-27

Specification: owner-approved increment 3 in `SPEC-grouped-review-materials.md`.
This extends the existing legal-corpus/editor work, preserving all unrelated tasks.
The owner approved the implementation plan on 2026-09-27 and requested continuation.
The focused task breakdown is recorded in tasks/todo.md for review before code.

**Delivery order and dependency checkpoints:**

1. **A readable preparation card for each original.** Add immutable preparation
   revisions referencing existing incoming IDs/checksums. Store a strict typed
   payload: reviewed display title, kind (`NORMATIVE`, `CLINICAL_REFERENCE`,
   `REFERENCE_FORM`), original group, known metadata and its provenance, missing
   fields, conversion identity, extraction scope/hashes and intended document
   parts. Missing fields are explicit, not defaulted dates. An offline input
   validator/importer creates preparations, never approvals. Metadata-only API
   reads expose the current preparation without loading raw bytes or full text.
   Original download routes and receipt-import retry behavior remain unchanged.
   Checkpoint: all 58 have a traceable card; failed/partial extraction remains
   visible; old APIs and import retries still pass.
2. **Human group confirmation of eight reference originals.** A separate immutable
   reference-review event records the selected preparation and raw hash, actor,
   time, group/batch, explicit declarations and idempotency request digest.
   Add `reference-review-preview` and `reference-review-events` routes under the
   existing editor group resource; normative approval routes are unchanged.
   Existing active LEGAL_EDITOR plus gateway-key authorization protects both.
   One explicit action confirms only the displayed reference originals, not their
   legal currency or complete extraction. A missing cover year stays visible.
   Checkpoint: synthetic API/UI end-to-end, replay, conflict and concurrency tests;
   no reference enters legal retrieval. No actual human event is submitted in smoke.
3. **Prepare normative candidates and their complete artifact mapping.** Reconcile
   heading identity with source evidence and current canonical keys. Parse all
   parts in code bundles; maintain an immutable preparation-to-part/version link
   for every intended part. Normalize scoped text, retain tables/structure, build
   source-linked fragments and feed existing strict corpus_loader checks.
   Every required date needs source evidence or explicit editor-supplied evidence;
   unresolved items remain preparation cards, not dummy LegalVersions. Editor
   corrections append a new revision and never rewrite original receipt metadata.
   Checkpoint: every original and part accounted for; canonical duplicates linked
   without losing alternate originals; all readiness blockers enumerated.
4. **Unified group completion and simple Telegram actions.** Preserve seven roots,
   pagination and back/cancel. Show full titles, original downloads, missing fields,
   normative-ready counts and reference-review counts. Use the existing normative
   confirmation for genuinely prepared versions and the separate reference action.
   Replace checksum-only hiding for prepared artifacts with explicit all-part
   completeness: a single prepared part must not conceal the rest. A changed
   preparation invalidates prior previews and is not covered by an earlier review.
   Checkpoint: complete clinical/healthcare mixed workflows, six legacy versions
   remain reachable, root still contains exactly seven groups.
5. **Verify and release through GitHub.** Run full unit/integration/type/lint/
   security/Compose gates on disposable infrastructure. Verify checksums and
   preparation coverage of the private package separately. After release review,
   migrate and deploy through the existing workflow, import preparations
   idempotently, then read-only check health, all pages, originals and both preview
   types. Publish exact ready/blocked/reviewed counts. Stop short of calling a group
   ready when required fields remain unresolved; never perform a lawyer's approval.

**Persistence/API decisions for this plan:**

- Additive preparation revisions, immutable part/version associations and separate
  reference-review events; no alteration of stored incoming metadata or weakening
  of LegalVersion requirements. Global legal materials retain platform scope,
  not a fabricated clinic tenant. Audit rows have real server-resolved actors.
- Revisions are ordered under a material lock; same input retries reuse the same
  checksum-bound result. All-part associations are checked against the declared
  preparation, canonical identity, original and normalized hashes.
- Reference confirmations use atomic per-preparation events, stable lock order,
  unique actor/idempotency keys and a stored request hash. Same-intent retry returns
  the original result; different-intent key reuse returns 422; stale/in-flight
  conflict returns 409. The exact displayed set must match, with no partial commit.
- Preparation input is strictly bounded and untrusted. Do not execute embedded RTF
  objects, arbitrary URLs or model instructions; no new fetch hosts, services,
  production converters, OCR/LLM dependencies or CI changes in this plan.
- Extend read responses additively with preparation/reference progress. Keep old
  download and legal-approval response shapes/error conventions intact. Declare
  a separate contract for metadata correction before exposing any new write route.
- New tables require matching models, Alembic constraints/append-only protections,
  runtime grants and migration tests. Record the critical contract in ADR-0046.

**Risks, sequencing and rollback:**

- Unknown consolidated-edition dates: resolve from evidence, otherwise explicit
  editor input; do not substitute enactment, import or maximum mentioned date.
- Multi-part originals and alternate copies: explicit coverage links, no dedup by
  act number alone or by one shared raw hash. Preserve source download access.
- Table/figure loss: distinguish extraction scope from original-file review; retain
  full originals and page/section caveats. Do not advertise text completeness based
  on a successful converter exit code.
- Shared migrations and group contracts are sequential. No independent agents or
  parallel writers are planned. Offline extraction can be inspected while awaiting
  review without mutating server state.
- Rollback uses the prior application revision and preserves additive records.
  Do not downgrade/drop review ledgers on production or erase human attestations.
  Old deployment reads the original inbox as before; no new automatic approval or
  new evidence-policy activation needs undoing.

After plan review, record focused tasks (at most about five files each), precise
tests and dependencies in `tasks/todo.md`; review them before implementation.

### Existing project decisions

- Legal Core — единственный источник нормативной истины; см. ADR-0001.
- Tenant context принадлежит серверу, а не клиенту/LLM; см. ADR-0002.
- Модульный монорепозиторий используется для MVP, но компоненты общаются через явные REST/MCP contracts.
- PostgreSQL — system of record; Redis не хранит единственную копию юридически значимого состояния.
- Legal updater создаёт immutable versions и не индексирует их до approval/regression gate.
- Единые семь групп редактора и явное подтверждение готового набора — ADR-0045.
  Порядок выпуска: API и транзакционные тесты → Telegram-навигация → quality/security
  gates → GitHub deployment → read-only проверка групп, файлов и health. Подготовка
  реквизитов входящих материалов остаётся отдельным этапом; автоматического approval нет.

## Task List

### Phase 0: Platform bootstrap

- Task 0.1: Репозиторий, ADR, project rules и quality configuration.
- Task 0.2: Legal Core live/readiness API contract с тестами.
- Task 0.3: Docker Compose для pgvector, Redis, MinIO и Legal Core.
- Task 0.4: CI quality gates и закрепление Hermes после upstream/security review.

### Checkpoint: Bootstrap

- Tests, lint, typecheck и `docker compose config` проходят.
- Health endpoints проверены runtime-запросом.
- В истории/fixtures/logs нет секретов и patient data.

### Phase 1: Identity, tenancy and Case Core

- Clinics/users/clinic_users, RBAC и server-side tenant context.
- Cases/facts/messages/audit с миграциями и tenant-negative tests.
- Missing-facts state machine и пересчёт состояния после новых фактов.
- Telegram skeleton создаёт case_id только через защищённый backend.

### Checkpoint: Case Core

- Clinic A не может читать/изменять Clinic B.
- По case_id восстанавливаются state transitions и actor/correlation metadata.
- Неполный синтетический кейс возвращает только необходимые вопросы.

### Phase 2: Legal corpus and retrieval

- Trusted sources и immutable legal versions с lifecycle.
- Ручной ingestion проверенного corpus, SHA-256 idempotency и raw object storage.
- Time-travel resolver и exact/FTS retrieval для `APPROVED` versions.
- pgvector/hybrid retrieval и evidence contract.
- MCP read tools с authz/contract tests.

### Checkpoint: Evidence

- Исторические даты возвращают правильную редакцию.
- Source card трассируется к реально возвращённому fragment/version.
- Неодобренные/неприменимые версии недоступны production retrieval.

### Phase 3: Risk and agent orchestration

- Versioned deterministic risk rules, reason codes, HIGH/CRITICAL escalation.
- Pseudonymization boundary до внешней LLM.
- Structured Research/Verifier contracts; claim coverage только по evidence.
- Hermes pinned и ограничен allowlisted MCP tools.
- Draft response card без автоматической отправки.

### Phase 4: Legal updater and regression gate

- Allowlisted discover/fetch/parse/version pipeline.
- Review queue, diff, regression gate и idempotent retries.
- ≥100 утверждённых синтетических regression cases.
- Promotion блокируется на P0 failure.

### Phase 5: Pilot operations

- Metrics/traces без raw PII, backup/restore drill, retention/deletion policy.
- Security, ПДн, врачебная тайна, локализация и legal review.
- Controlled pilot для 3–5 клиник с обезличенными сценариями.

### Proposed free-pilot and practical-case research track

`SPEC-free-pilot-practice-research.md` defines a controlled free pilot and a reviewed practical
scenario library. Product approval for the owner-granted, time-limited pilot was recorded on
2026-08-31. Public consultations may reveal taxonomy candidates, but are neither a legal source
nor a scrape/train corpus. Legal/security approval remains required before a scenario library or
external integration starts; the pilot does not loosen the evidence or tenant gates.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Ошибочная/устаревшая норма | Critical | Approved-only time-travel corpus, evidence, verifier, regression gate |
| Cross-tenant утечка | Critical | Server-owned context, backend scope, RLS, negative security tests |
| PII/медданные у внешней LLM | Critical | Local redaction, minimization, explicit provider/compliance review |
| Чрезмерная автономность агента | High | Allowlisted read/write tools, policy checks, no auto-send/auto-approve |
| Нестабильный upstream Hermes | High | Pin commit/release и compatibility tests |
| Неясные российские compliance-требования | High | Блокировать реальный пилот до профильного review |
| Источник изменил формат/недоступен | Medium | Raw preservation, parser failures → REVIEW_REQUIRED, retry/backoff |

## Owner-approved audit remediation — 2026-09-30

The owner requested remediation after the full bot audit and explicitly approved the
required internal analysis authorization and Redis-backed rate/concurrency limits.
The work remains split into independently deployable vertical slices; it does not
authorize automatic legal approval, direct Telegram egress, patient-data collection,
or a production launch with real case data.

1. **Transport recovery first.** Keep Telegram behind the internal proxy only. Use a
   validated multi-outbound VLESS `urltest` pool, probe actual proxy egress without
   writing a bot token to logs, and make the gateway withdraw readiness/restart after
   bounded consecutive Bot API failures. A direct fallback is forbidden.
2. **Protect the analysis boundary.** Require the existing internal service key for
   analysis-context and analysis-submission paths, update the orchestrator client,
   preserve the tenant actor check, and add negative authorization tests. This is
   documented as an ADR because it changes a protected internal API contract.
3. **Stop global UI stalls.** Keep per-conversation ordering but acknowledge callbacks
   immediately and move document parsing and other long work to bounded durable paths.
   Share lifecycle-managed HTTP clients, use short interactive deadlines, and enforce
   a Redis-backed per-user/clinic limiter. No global concurrent-update switch that
   would break Telegram conversation semantics.
4. **Complete the owner-approved intake.** Add the "what the clinic already did" fact,
   a case-scoped anonymised evidence path with explicit privacy limits, exact-date
   recovery, and visible Back controls. Schema additions require migration, retention,
   tenant and no-raw-PII tests.
5. **Make legal readiness demonstrable.** Finish P8–P11, add reviewed coverage mappings
   for supported groups, and introduce lawyer-reviewed synthetic gold cases as a release
   gate. The evaluator must abstain outside reviewed coverage; reference reviews remain
   separate from legal approval.

### Audit remediation checkpoints

- Transport: a live failover pool passes repeated `getMe` probes through the proxy;
  a simulated failure removes gateway readiness and is restarted without direct egress.
- Authorization: a Telegram user ID without the internal key receives `403`; the
  orchestrator path with the key remains successful.
- Responsiveness: a deliberately blocked heavy action does not hold unrelated menu/
  status actions; limits return an explicit retry response rather than queuing forever.
- Legal quality: every supported group has lawyer-reviewed synthetic acceptance cases;
  unprepared or inapplicable corpus material still produces abstention.

## Active vertical slice: administrator intake and evidence foundation

The approved implementation order is:

1. Freeze the administrator intake, canonical report and threat model contracts.
2. Add identity/tenant, cases, facts, reports and audit persistence with migrations.
3. Expose idempotent Case Core REST endpoints and prove tenant isolation.
4. Render Telegram and PDF from one immutable report schema.
5. Ingest the first official corpus, then approve only verified artifacts.
6. Add effective-date, approved-only fragment retrieval.
7. Connect the Telegram conversation to Case Core.
8. Enable recommendations and draft responses only after evidence and verifier gates pass.

This slice is specified in `SPEC-case-intake-report-legal-corpus.md` and ADR-0003/0004.

## Subscription access decision

The service is a SaaS assistant for clinics, not a provider of a customer-facing lawyer.
Before a clinic administrator can use the bot, Legal Core must resolve an active entitlement for
that exact user and clinic. The first release contains entitlement persistence, expiry/suspension
enforcement and owner-controlled provisioning through Telegram only. Legal Core checks the
configured owner ID server-side and the command accepts a target Telegram ID; it does not grant
legal-editor rights or select among multiple target clinics. The release intentionally excludes
payments, card data, provider webhooks, invoices and self-service purchase; those require a
separate product and security decision.

## Open Questions

Критичные для bootstrap вопросы не требуются. Вопросы о Hermes pin, provider, trusted sources, thresholds, retention и pilot quality gate должны быть решены до соответствующих фаз, а не угадываться сейчас.

## Proposed next slice: evidence-gated analysis and updater

`SPEC-evidence-risk-agents-updater.md` is the approval package for phases 3–4. It proposes a
limited source scope, deterministic versioned risk policy, verifier contract, optional disabled
Hermes/provider boundary and a legal-updater promotion pipeline. It does not enable a legal
recommendation, risk conclusion, external draft, source approval, LLM connection or payment
feature. Implementation begins only after its legal/product/security approval record is complete.

## Active slice: platform legal-editor review workspace

`SPEC-legal-review-workspace.md` replaces the owner-only metadata view with a bounded human
review workflow for an active platform `LEGAL_EDITOR`: candidate list → immutable official
artifact/fragment inspection → explicit checksum-bound attestations → existing audited approval.
It does not change sources, approve any version automatically, activate risk/Hermes or expose
tenant data. The incremental order is contract/authz and stale-identity guard → Telegram review
workspace → qualified human review of the five candidates → approved-library regression check.

## Completed slice: durable administrator draft cards

`SPEC-durable-telegram-drafts.md` replaces the gateway-only in-memory draft as the source of
truth. Build order is: tenant-scoped draft persistence and contract → authorisation/idempotency
tests → Telegram save/resume/list/switch controls → restart and deployment verification. It was
deployed on 2026-08-31, keeps case creation at final confirmation and does not change evidence,
risk or recommendation gates.

## Controlled GitHub deployment foundation

The public repository `Danil-super/Legal_ai` is the only application source for the VPS. Every
pull request and update to `main` must pass the existing quality and integration suites, a Python
dependency audit and CodeQL scanning before a `main` deployment is allowed. GitHub Actions never
receives the Telegram token, database passwords or MinIO credentials; those remain in a
root-readable runtime file on the VPS.

The production job uses a dedicated, key-only `deploy` account with a forced command. Its only
permitted operation is to request a validated commit already reachable from `origin/main`; a
root-owned deployment script performs the checkout, Compose launch and readiness check. The
initial deployment gate is disabled until a human configures the real Telegram owner/token on the
host. Hermes/LLM and maintenance profiles remain disabled.

Rollback is an explicit, audited workflow action rather than an automatic database downgrade.
Schema changes therefore require backward-compatible deployment discipline and a restore plan.

## Active remediation: complete bot interactions (2026-09-12)

The owner requested all six audit fixes in `SPEC-bot-reliability.md`. Implementation lives on
`fix/bot-reliability`, based on the actual production/GitHub revision `f9e1165`, without replacing
the older local main worktree. Three implementation agents and a separate adversarial reviewer
worked in isolated worktrees; root integrated, reviewed and reran the combined tests.

Implemented: approved-copy retrieval and complete human excerpt review; durable two-consumer
analysis queue; opt-in versioned early triage; dialogue mode isolation; assigned/resolved lawyer
workspace and chronological history; nonblocking PDF delivery. Regression review additionally
closed retention replay/purge defects, inaccessible-notification starvation and typed UUID false
positives in the provider privacy guard.

Release order: clean PostgreSQL/MinIO verification → GitHub PR quality gates → human review and
release approval → existing main autodeploy → read-only health/migration/queue checks → explicit
risk-policy activation if approved → owner/lawyer private-chat smoke test. Normative approvals
are never part of deployment: lawyers must review and approve the relevant legal versions.
# Unified entry and action-plan report: authorised continuation 2026-10-13

Order: reproduce entry divergence → shared V2 collection and fail-closed save →
canonical-only report view → full integration/visual/review gates → reviewed release
and live synthetic checks. Report implementation and independent scenario tests
use separate branches; main owns intake/clarification and reconciliation.
No storage/schema/model/policy changes. Existing incomplete project work below
is retained, not closed or replaced by this task. Contract: ADR0082 and approved
SPEC-report-action-plan.md; executable checklist is the new section in tasks/todo.md.

## Extended audit continuation (2026-10-14)

Continue the same release audit, preserving every earlier incomplete task. Order:
clean-runner PDF dependencies → reproduced gateway dispatch/stale-action/cache
boundary failures → Unicode upload wire contract → report allocation and expired
reference-content guards → combined regression/CI/review → separately approved release.
Root owns CI and upload wire; isolated gateway and Core agents own their narrow
reproductions/fixes. Reference package independence requires an explicit owner
auth-contract choice; no roles or legal approvals are changed implicitly.
