# Implementation Plan: Dental Legal AI MVP

## Intake dates and transient message cleanup — 23 October 2026

Owner reproduced stale waiting messages and a redundant date question on a new
quick case. Reuse the bounded, previously approved temporal-context increment
(ADR0095), not the unfinished multi-field proposal overhaul. Bind initial model
interpretation to Core's persisted operation calendar and distinguish complaint
date from service date. Preserve uncertainty and the final summary confirmation.

Record exact transient message IDs and clean them only after durable save/staging
and successful next-prompt delivery (ADR0097). Preserve narratives, files and
summaries; no history sweep or guessed IDs after restart. Reproduce quick/guided
and failure/replay boundaries, then complete independent review and full isolated
quality gates. Production release remains a separate owner decision. No provider,
model/effort, risk/auth, normative approval or schema change is included.

## Intake waiting and active-draft navigation — 20 October 2026

Owner report after PR105: unclear waiting, stopped/resume detour and apparent
repeat of the source description. Production metadata distinguishes explicit
cancellation from model failure and shows that the later EVENT belongs to a
different old draft. Preserve both drafts; do not rewrite real user data.

1. Independently reproduce manual detour and source-preservation controls.
2. Clarify pending copy, remove normal manual detours, resume legacy manual and
   initial failures automatically into the same shared missing-facts wizard.
3. Prevent stale controls silently replacing the active draft; /menu remains
   the explicit way to choose another saved draft (ADR0094).
4. Verify quick/guided, failure/restart and final-summary/report regressions;
   complete quality gates and independent review before proposing release.

One final summary confirmation remains. No provider/model/risk/legal-approval
changes and no production release are implied by local implementation.

## Explicit factual coverage correction — 20 October 2026

The owner requested continuation after the production quick/guided smoke exposed
redundant factual questions. Fix the initial interpreter's blanket instruction to
omit negated facts and its uninformative generic safety dictionary schema. Keep
the existing optional proposal contract, single model call, source grounding,
staging, final summary consent and all Core validation unchanged (ADR0093).

1. Independently reproduce the actual model-facing prompt/schema defects.
2. Explain explicit NO versus missing/unknown information and expose the existing
   closed partial screening shape, deriving it from Core's existing schema.
3. Verify complete, partial, ambiguous and conflicting fictional inputs; prove
   that staging complete suggestions avoids redundant questions. Do not claim
   mock tests prove live model extraction completeness.
4. Run quality gates and independent review; test the current provider/profile on
   fictional cases before requesting release. No model/effort/risk/auth changes.

## Conversational intake correction — owner request 20 October 2026

One ordinary flow: description → missing factual questions → one final summary
confirmation → queued recommendations or lawyer routing. The manager must not
operate model polling or approve every interpreted field. Interpretations remain
provisional draft data until that final explicit confirmation. ADR 0092 and the
automatic-delivery ADR define the additive server/Telegram boundaries.

Implementation and independent Core/Gateway reviews completed on isolated branches.
PR103/104 passed their exact-head checks, were owner-authorized and released through
successful main CI deployments (39385721439/39386050888). Revision f5c1fcd passed
both owner-only fictional live entries: READY/LOW, verified applicable sources and
actual PDF delivery. The fresh full local suite passed4149tests without skips.
The live smoke also exposed incomplete initial extraction: quick9follow-ups,
guided14. This establishes the paths, not minimal questions or full legal quality.
Preserve all earlier work below and the explicit coverage correction above.

## Pre-report case understanding — spec approved 2026-10-15

Contract: SPEC-pre-report-intake-understanding.md, owner approval15Oct.
This extends the existing case-readiness/FC work, not another product. Preserve
all earlier unfinished work below. Owner approved execution after clarifying
Hermes' semantic role on15Oct: it interprets the narrative and subsequent answers,
suggests factual mappings/questions and the next step; Core enforces the boundaries.
Execution follows the PRC dependency order. Approval is not a production deployment or legal approval.
The earlier isolated UI fix6624084 remains unreleased.

Execution checkpoint15Oct: PRC1/2 contracts/storage/runtime and PRC3–5 receipt,
queue and orchestration readers are implemented with isolated independent reviews.
The exact Docker COPY-set regression is fixed and a real gateway image builds;
immutable6eb1d2c full PostgreSQL/MinIO suite passes2904 tests without skips.
Worker follow-up ebb5520 additionally refreshes/fences the current lease before I/O
and forwards only the stored result version; its independent fresh PostgreSQL42
tests pass. Final immutable ebb5520 full run:2910 passed/0 skipped,194.05s.
The complete Telegram rollback reader is not established until PRC7/10.
PRC6 late producer is implemented at8a86ce9 with integrity/concurrency/access fixes
through94a9751. It persists actual verifier/risk identities without intermediate
reports; only READY renders the real final PDF. Independent review reproduced
three defects in the initial writer, then verified the fixes (9 tests), and a
separate real lock-wait access matrix passed20 tests. Author focused regression:
94 passed. Full94a9751 verification repeated after the execution environment
reset:2938 passed/0 skipped,171.27s, with fresh migrated PostgreSQL and MinIO.
Ruff/all-three-service mypy143/Compose pass; the interrupted run is not counted.
PRC6a Core cd7646a + transport f87d41c implement honest pre-context limitations,
urgent routing and authenticated retained-result replay. Independent final review
verified86 focused tests with no outstanding Required/Critical finding. Immutable
f87d41c full PostgreSQL/MinIO run:2975 passed/0 skipped,189.68s; Ruff/mypy143/Compose
pass. The new gate preserves safe-stop and does not repeat model work on replay.
PRC6b recovery7c2dbef (ordering prerequisite9a6ec75) binds intermediate origins
and draft creation to immutable audit, preserves attempts and original files,
and checks latest source/access/retention before replay. Independent review found
same-origin receipt substitution; corrected regression and follow-up41 tests pass.
Author99 focused tests pass. Final immutable tree91764b7/commit7c2dbef:
3010 passed/0 skipped,224.57s on isolated PostgreSQL+MinIO; quality checks pass.
PRC6c codec/client fd68e8f and runtime/router f459747 establish57-byte source-bound
Telegram callbacks. Actual client/Core/PostgreSQL recovery survives lost replies
without duplicate drafts; independent review134 tests includes current-source and
same-clinic creator negatives. Full immutable tree bb5307c (f459747):3053 passed/
0 skipped,313.72s, isolated PostgreSQL+MinIO; quality checks pass.
PRC7a b6df566 adds source-bound factual controls; PRC7b f48320b implements the
explicit report-free initial writer. PRC7c3868e20 closes the malformed returned-
draft gap found by independent review using Core's shared partial-draft validator.
Independent7b:82 real-PostgreSQL tests pass;7c:307 pass/14 PG skips plus3522 exact
validator outcome comparisons. Author full3868e20/tree eda5a57:3096 passed/0 skipped,
341.59s on fresh PostgreSQL+MinIO; Ruff/mypy143/Compose and actual Gateway image pass.
PRC7d client8b5bb30, queue64c4738, adapterd488314 and runtimef83a49f complete the
explicit initial receipt consumer. Independent queue113 and whole-Gateway/runtime
934 actual PostgreSQL tests pass; the latter also proves a synthetic initial→
NEEDS_FACTS→bound correction→READY/verified ACTION/PDF chain. Review fixed creator
access after waits, future-date acceptance and configured startup's duplicate CTA.
Full f83a49f/tree fec864a:3145 passed/0 skipped,516.72s on fresh PostgreSQL+MinIO;
Ruff/mypy144/Compose and actual Gateway Docker image pass. The earlier full run's
one failure is preserved in the audit, not counted as success.
NEXT: PRC8–11 confirmed understanding/shared dialogue acceptance. Ordinary producer
activation remains gated on their completion; both ordinary entry paths are not
proved by explicitly seeded receipt2 acceptance. PRC8a initial narrative interpreter
a1b3888 and nested-UNKNOWN storage fix14f3755 are implemented but not wired into
ordinary intake. Independent interpretation464 +12 attack tests and storage107
tests pass. Final immutable14f3755/tree67b3aec:3192 passed/0 skipped,493.28s on fresh
PostgreSQL+MinIO; Ruff/mypy145/Compose and actual Core/Agent containers pass.
PRC8/9/10 remain incomplete: subsequent answers and explicit user confirmation are
not provided by this internal module. The next bounded runtime checkpoint through
75b523f implements private source/lease resolution, metadata-only Agent invocation,
shared two-slot background execution and owner POST/GET/DELETE operations with
durable cancellation. Independent first-boundary review192 tests passed and fixed
an exception-retention defect; final background/actor review224 tests/0 skips
passed with no remaining Required/Critical. Final immutable b48c11b/treef436cd1:
3292 passed/0 skips/534.29s on fresh PostgreSQL/MinIO; Ruff/mypy149/Compose and
actual Core/Agent builds pass. Separately reviewed startup pin update preserves
personal M1; the stronger actual-router canary detects hidden nested prefixes.
Evidence: docs/audit/pre-report-understanding-runtime-2026-10-16.md.
The subsequent PRC10 initial-narrative slice now provides durable quick source,
audit-bound atomic consent/replay, source-bound recovery and short Telegram
refresh/manual/cancel/edit/menu controls. The opt-in common summary preserves
UNKNOWN/APPROXIMATE and selects receipt2. Legacy material resume is unchanged.
Both actual PTB/HTTP/PostgreSQL entries reach one report-free case; model output
and Telegram delivery are synthetic. The bd86049 full gate was interrupted and
does not establish clearance. The owner rejected an initial-only test release.
Subsequent answers now have separate question/answer-bound operations, strict
target values, explicit consent and durable cancellation; all ten factual question
families pass real Core/Agent HTTP tests and both full PTB entries pass against
PostgreSQL. Full immutable86e2c80 PG/MinIO verification passed:3965 tests,
0 failures/errors/skips,372.88s. Five subsequent independent compatibility guards
also pass on identical runtime source. Ruff/mypy171, Compose, Bandit, dependency
audit and three offline container smokes pass. GitHub CI/release/live acceptance
remain separate gates, not implied by this local result.
Ordinary default startup is still unchanged. Current evidence and limits:
docs/audit/pre-report-consent-and-telegram-2026-10-16.md.
The V2 writer is opt-in/internal only: ordinary queues remain V1. No server
migration or complete pre-report dialogue is activated. See
docs/audit/pre-report-late-preparation-2026-10-15.md.

### Architecture and dependency order

One user flow: describe → confirm understood facts → complete missing facts →
confirm summary → verified report or an understandable limitation. Legal Core
remains authoritative. The model proposes facts, not law or risk classification.

1. **PRC1–PRC3: prove persistence and reader compatibility first.** Existing
   AnalysisJob has a required case FK and finalized-case guard; it is not a
   general draft queue. Existing Telegram workflow submission also unconditionally
   renders an intake PDF and has a required report FK. Both that initial receipt
   and late analysis must support intermediate preparation, not merely hide PDFs.
   Define a bounded versioned result: NEEDS_FACTS / LIMITED / READY / HUMAN_REVIEW,
   where only a genuinely final report exposes a PDF. Keep legacy V1 shapes and
   stored reports. Establish reader-compatible baseline before new producers.
2. **PRC4–PRC7: finish one late-clarification vertical path.** Core validates and
   durably stores the intermediate result against actor/clinic/case/job and fact/
   evidence/policy fingerprints, without calling CaseReport/PDF creation. Queue,
   worker, notifications and UI consume that result as completed preparation,
   not a failed task or legal READY. An intermediate-bound confirmation opens
   the same form; finalized case snapshots remain immutable, with explicit new
   draft confirmation and unchanged attachment ACLs. Then enable report-free
   initial workflow receipts. Legacy report-bound recovery remains supported.
3. **PRC8–PRC10: add early understanding and shared pre-report form.** Save an
   actor-owned draft before a bounded Hermes request. Only permitted existing
   V2 fields with explicit source-text grounding may be proposed; proposals remain
   separate from confirmed facts. User accepts/edits the compact review before
   prefilling. Both entries use the same targeted question/answer form. A course
   of treatment is not silently assigned one universal date; ask which episode
   the complaint concerns. Invalidate answers when service scope changes.
4. **PRC11–PRC12: prove the complete flow and release.** Fresh fictional quick/
   guided cases, late clarification, UNKNOWN, urgency, restart/double click and
   source-grounded final plans; full PostgreSQL/MinIO and existing security/CI,
   independent review, approved immutable-head release and actual persisted
   live results. Process-local model success is not the completion criterion.

Checkpoints follow PRC1–3, PRC4–5, PRC6/6a/6b/6c, PRC7–9 and PRC10–11;
exact tasks are in tasks/todo.md. PRC6a handles pre-context limitations;
PRC6b/6c cover the Core/gateway source-bound recovery boundary.
Shared contracts/storage/writers are sequential. An independent reviewer may use
an isolated branch; no parallel implementation of the same contracts.

Read-only next-boundary review16Oct identified implementation obligations, not
newly active endpoints: preserve the existing aggregate two analysis slots with
shared admission acquired before job claim; keep private operation tokens inside
Core↔Agent; use owned current lease/revision/hash/access/safe-stop context before
model I/O, rather than exposing a source-only model endpoint under the shared key.
Current /cancel/menu only pause Gateway RAM and need an owned durable operation
fence for the new mode. Quick entry must save its owned description before model
proposals, not only after acceptance. Preserve Core's sanitized validation error
style and avoid the case_api↔intake_preparation import cycle via a focused router
or local import. Implement contracts/client, private context, shared admission,
public actor operation API and confirmed UI in bounded≤5-file increments; no
producer activation or implicit scope/capacity increase follows this review.

### Storage decision gate and contracts

- Planned expansion (no new table): nullable lease_token/lease_until on
  IdempotencyRecord for the new operation scope; receipt_version(default1) on
  TelegramCaseWorkflow, report_id nullable only for a report-free V2 receipt;
  result_version(default1) on AnalysisJob. Keep existing tenant/case/actor FKs
  and legacy non-null report semantics. PRC1 specifies exact constraints;
  PRC2 proves the Alembic expansion before producers. These explicit existing-
  table changes are within the approved plan; exact constraints and proofs precede writes.
- Reuse existing draft/idempotency/job storage only after a concrete proof.
  Early understanding cannot borrow a fake finalized case, a fake report FK,
  an unowned cache or an unlimited in-memory task. Test atomically claimed intent,
  stable server-derived actor/clinic/draft/revision/content/version intent and
  token-fenced completion. Atomically reserve/commit before network; complete
  after reauthorization/revision/expiry checks in a short transaction. No DB lock
  across Hermes. A crash/unknown outcome expires to manual continuation or an
  explicit new user intent, never blind model re-execution or endless autoretry.
- IdempotencyRecord has no automatic TTL. If it contains proposals or intermediate
  private context, its lifetime, expiry-before-replay and cleanup must explicitly
  follow the owning draft/case. The early-operation resource_id binds draft.id;
  its separate resource_type needs expiry/replay and purge support, not reliance
  on the existing TELEGRAM_INTAKE_DRAFT-only scrubber. Keep only bounded necessary
  proposals/metadata, not another raw description/evidence copy in a queue.
  Scrubbing preserves non-content replay tombstones.
  Retention must also cover abandoned/in-progress records, not only successful ones.
- PRC1 is a fail-fast gate: if safe bounded draft work cannot fit existing storage,
  present the exact additional schema/authority needed before building it. A new
  table/storage system is not approved by this plan. Existing-table additive
  columns/FK nullability changes listed above need an explicit schema diff with
  Alembic/RLS/retention/rollback tests before enabling producers; no hidden migration.
- New result contracts are opt-in and strictly validated, not an unannounced
  change from required report to null. Versioned idempotency/result namespaces,
  active-job collision handling and latest-job queries must not reuse legacy
  cached output as fresh preparation or run two active analyses of one snapshot.
  Pin a tested reader-compatible rollback minimum; b052f40 is not assumed safe
  after writing new workflow/result variants. Do not downgrade or erase records.
- Revalidate actor, subscription, creator/tenant, revision and expiry at read,
  model completion and confirmation; ignore stale completion after cancel/edit/
  archive/submit. Restart recovery preserves the durable intent, not gateway RAM.
- Report-free recovery binds operation/result ID and hash, frozen case facts
  and available evidence/policy/context fingerprints against a retained server
  result, not client-selected questions or a fake report. No invented run or
  evidence ID for a gate that has not executed. Keep legacy case+report recovery;
  version the new callbacks within Telegram's64-byte bound.
- Unknown event dates, unknown safety answers and no evidence can stop before
  submit_analysis. Classify these preparation outcomes in Core as LIMITED or
  NEEDS_FACTS, not broad exception swallowing. Do not automatically reopen attempted
  questions in confirm_case or strip UNKNOWN during prefill. Exact-date/evidence/
  risk gates remain closed: LIMITED is neither legal READY, a known negative
  fact nor an invented high-risk decision. Offer the existing authorized human path.
- Keep max3 questions per batch,5 services and11 answers. Exact confirmed values
  and attempted UNKNOWN/APPROXIMATE are not re-asked. Confirming proposed dates
  creates user-statement provenance, not automatic VERIFIED legal facts.

### Safety, rollout and risks

HIGH/CRITICAL and v4/10k stay unchanged and must bypass optional preparation waits.
Manual continuation remains available while understanding is pending/unavailable;
current policy evaluates user-confirmed facts, never silence or model inference.
Model/provider/high,55/65/115/125/180 fences, tool-free configuration, trusted sources,
roles, files-to-model prohibition and patient-send prohibition are unchanged.
No new model service, payment, PII category, auto approval or reference activation.

Reader-only compatibility may be deployed before producers, default-disabled until
the reviewed complete slice is approved for activation. Every new critical result
and recovery contract has an ADR and tests. Existing normative/reference ledgers,
old case/report snapshots and material ownership are checked before/after release.
The existing quality bar is not lowered; a new question state does not make legal
evidence applicable, convert uncertain dates into exact dates or guarantee advice.

Commands and quality gates remain those in the approved spec and AGENTS.md.
No migration/feature activation is authorized merely by recording this plan.

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
