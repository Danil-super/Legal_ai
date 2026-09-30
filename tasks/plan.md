# Implementation Plan: Dental Legal AI MVP

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
