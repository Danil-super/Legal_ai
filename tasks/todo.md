# Dental Legal AI task list

## Complete package preparation: discovery, 2026-09-27

- [x] Owner confirmed separate normative/reference confirmation within seven groups.
- [x] Inventory all 58 originals; extract all 51 RTF; inspect all seven PDF covers
  and two additional pages with missing text layers. Record limitations and all
  materials in `docs/legal-review/package-preparation-2026-09-27.md`.
- [x] Review increment 3 in `SPEC-grouped-review-materials.md`, including
  additive storage for preparation and separate human reference-review events.
- [x] Owner approved the implementation plan and requested continuation.
- [x] Owner approved tasks P1–P11 for implementation on 2026-09-27.
- [ ] Prepare, test and deploy the complete workflow; do not equate this local
  discovery record with production readiness or human legal approval.

## Complete package preparation: implementation tasks

Status: owner-approved task breakdown; no legal approval/deployment implied.
Paths below are relative to `services/legal_core/` unless stated otherwise.
Every code task follows red → green tests; only synthetic fixtures enter git.

### P1. Validate an immutable preparation input

- [x] Acceptance: strictly typed, bounded original/hash/title/kind/group, source
  evidence, extraction limitations and intended parts; unknown dates stay null.
  Reference inputs cannot claim normative readiness or approval.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_material_preparation.py`.
- Files (2): `src/legal_core/material_preparation.py`, `tests/test_material_preparation.py`.
- Dependencies: approved tasks; size S.
- Evidence: 13 focused tests; full local suite 980 passed / 91 dependency-gated
  skips; focused Ruff and mypy passed (2026-09-27). No deployed behavior yet.

### P2. Persist preparations without altering receipts

- [ ] Acceptance: additive model and Alembic migration, immutable revisions with
  material locking and checksum-bound retry; original receipt import unchanged.
  Runtime grants exclude update/delete of preparation records.
- Verify: disposable PostgreSQL migration and focused persistence tests;
  `.venv/bin/python -m pytest services/legal_core/tests/test_material_preparation_persistence.py services/legal_core/tests/test_review_material_persistence.py`.
- Files (5): `src/legal_core/models.py`, new migration,
  `src/legal_core/runtime_db_role.py`, `src/legal_core/material_preparation.py`,
  `tests/test_material_preparation_persistence.py`.
- Dependencies: P1; size M.

### P3. Import the private preparation package safely

- [ ] Acceptance: explicit bounded CLI input, all originals accounted for, repeat
  run creates no duplicates, paths/checksums verified, missing metadata retained;
  no LegalVersion or approval created by this input path.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_preparation_import.py`;
  separate read-only validation of all 58 private originals and diagnostic output
  containing counts/hashes/reasons only, never full document contents.
- Files (3): `src/legal_core/preparation_import.py`, `tests/test_preparation_import.py`,
  `docs/legal-review/package-preparation-2026-09-27.md` (repository-root path).
- Dependencies: P2; size M.

### P4. Expose preparation cards read-only

- [ ] Acceptance: additive editor API returns current title/classification/known
  metadata/missing fields and original download identity. Existing authorization
  enforced; metadata queries do not fetch full text/raw bytes.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_review_material_api.py services/legal_core/tests/test_editor_groups_api.py`.
- Files (5): `src/legal_core/legal_api.py`, `src/legal_core/api_contracts.py`,
  `src/legal_core/editor_groups.py`, `tests/test_review_material_api.py`,
  `tests/test_editor_groups_api.py`.
- Dependencies: P3; size M.

### Checkpoint A: all originals have preparation cards

- [ ] Original 58 checksums and receipt retries preserved; cards enumerate actual
  missing fields and extraction limitations; no accidental normative promotion.
- [ ] Focused tests, Ruff and mypy pass; review results before next slice.

### P5. Persist separate reference-review events

- [ ] Acceptance: separate append-only actor/time/preparation/hash/batch ledger,
  uniqueness and retry constraints; no LegalVersion/source changes; actor resolved
  by existing server-side LEGAL_EDITOR authorization.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_reference_review_persistence.py` on disposable PostgreSQL.
- Files (5): `src/legal_core/models.py`, new migration,
  `src/legal_core/runtime_db_role.py`, `src/legal_core/reference_review.py`,
  `tests/test_reference_review_persistence.py`.
- Dependencies: P2, checkpoint A; size M.

### P6. Confirm an exact reference group through the API

- [ ] Acceptance: preview exact ready/blocked/already-reviewed originals; explicit
  attestation and stable idempotency key; stale/concurrent changes never cause
  partial review; clinical PDFs and form remain excluded from legal retrieval.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_reference_review_api.py`;
  include authorization, replay/payload mismatch, concurrency, rollback and retrieval negatives.
- Files (5): `src/legal_core/reference_review.py`, `src/legal_core/api_contracts.py`,
  `src/legal_core/legal_api.py`, `tests/test_reference_review_api.py`,
  `docs/adr/0046-material-preparation-and-reference-review.md` (root-relative).
- Dependencies: P5; size M.

### P7. Present reference confirmation and preparation cards in Telegram

- [ ] Acceptance: same seven roots, clear titles and original downloads, back/cancel
  throughout, distinct norm/reference buttons with exact counts; stable retry key
  after timeout; absent publication/cover dates displayed honestly.
- Verify: `.venv/bin/python -m pytest services/gateway/telegram/tests/test_legal_library_runtime.py`;
  synthetic Core-response rendering and callback flow, no real Telegram sends.
- Files (2, root-relative): `services/gateway/telegram/src/telegram_gateway/legal_library_runtime.py`,
  `services/gateway/telegram/tests/test_legal_library_runtime.py`.
- Dependencies: P4, P6; size S.

### Checkpoint B: reference review works without normative promotion

- [ ] End-to-end synthetic group review is durable, idempotent and auditable;
  old legal-approval flows still work and reference material is not legal evidence.
- [ ] API and gateway regression suites pass; review results before next slice.

### P8. Prepare complete normative candidates and document parts

- [ ] Acceptance: source URL bound to matching heading, full canonical identity,
  evidenced edition/publication/effective dates, source-linked fragments and
  scoped normalized hashes. Code-part boundaries cannot match repeal notices;
  missing evidence yields an explicit blocker, never guessed metadata.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_normative_preparation.py`;
  separately reconcile all 50 private legal originals with current canonical acts.
- Files (4): `src/legal_core/normative_preparation.py`,
  `tests/test_normative_preparation.py`, `src/legal_core/material_preparation.py`,
  `docs/legal-review/package-preparation-2026-09-27.md` (root-relative).
- Dependencies: P3, checkpoint B; size M.

### P9. Bind every prepared part to a guarded corpus version

- [ ] Acceptance: additive immutable part/version associations, exact-original and
  canonical/text checks; existing corpus loader guards reused; one prepared part
  never hides the other parts; no automatic APPROVED state.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_prepared_material_versions.py`;
  disposable PostgreSQL, failed association rollback and repeated import.
- Files (5): `src/legal_core/models.py`, new migration,
  `src/legal_core/runtime_db_role.py`, `src/legal_core/normative_preparation.py`,
  `tests/test_prepared_material_versions.py`.
- Dependencies: P8; size M.

### P10. Reconcile unified group completeness

- [ ] Acceptance: original downloads retained; canonical alternate copies linked;
  all-part coverage replaces checksum-only suppression for prepared bundles;
  mixed reference/legal progress and blockers determine accurate group completion.
- Verify: `.venv/bin/python -m pytest services/legal_core/tests/test_editor_groups_api.py services/legal_core/tests/test_reference_review_api.py`.
- Files (5): `src/legal_core/editor_groups.py`, `src/legal_core/group_approval.py`,
  `src/legal_core/reference_review.py`, `tests/test_editor_groups_api.py`,
  `tests/test_reference_review_api.py`.
- Dependencies: P9, P7; size M.

### Checkpoint C / P11. Package verification and release

- [ ] Full `.venv/bin/python -m pytest`, Ruff, mypy for Core/gateway, Compose config,
  disposable PostgreSQL/MinIO integration and existing security gates pass.
- [ ] All 58 originals/parts accounted for; exact ready/blocked/reviewed counts
  reported. Unresolved required dates/text coverage prevent a false all-ready result.
- [ ] Human release review → existing GitHub deployment → idempotent preparation
  import → read-only production health/artifact/group/preview verification.
  Never submit a lawyer approval as a smoke check or drop ledgers during rollback.
- Files (up to 3, root-relative): `tasks/todo.md`, package verification report,
  operator instructions in existing `docs/` conventions; no CI redesign.
- Dependencies: P10; size M.

## Attorney package review, 2026-09-25

- [x] Grouped editor inbox with counts, filters, pagination and preserved originals.
- [x] Retry-safe import accepts safe files with explicitly unresolved provenance.
- [x] Deploy and import all 58 files; verify every stored/downloaded checksum.
  Production import completed on revision 2cd32d6: all 58 originals, retry without
  duplicates and every downloaded checksum verified (2026-09-25).
- [ ] Complete metadata preparation and explicit human approval for all legal copies.
  Receipt, grouping and file download are not normative approval.

## Simplified groups and human batch approval, 2026-09-27

- [x] Seven-group landing page without a separate six-document queue or mixed file list.
- [x] Prepared versions and incoming originals share the same subject groups.
- [x] Explicit ready-subset confirmation with immutable individual audit and safe retry.
- [x] Authorization, stale membership, concurrent confirmations and atomic rollback tests.
- [ ] Full quality/security gates and GitHub deployment.
- [ ] Read-only production verification; do not approve actual laws during smoke tests.

## Task 0.1: Зафиксировать bootstrap-контракт

**Acceptance criteria:**
- [x] Есть capability map, ADR Legal Core и ADR tenant context.
- [x] Есть project rules, spec, dependency-ordered plan и риски.

**Verification:**
- [x] Артефакты согласованы с разделами 0, 4, 19, 25–27 ТЗ.

**Dependencies:** None

**Files:** `AGENTS.md`, `CAPABILITY_MAP.md`, `SPEC-platform-bootstrap.md`, `docs/adr/*`, `tasks/*`

## Task 0.2: Реализовать Legal Core health API

**Acceptance criteria:**
- [x] `GET /health/live` имеет стабильную typed response schema.
- [x] `GET /health/ready` проверяет зависимости и сообщает degraded state.
- [x] Endpoint'ы документируются OpenAPI и не требуют auth.

**Verification:**
- [x] Focused/full pytest проходят.
- [x] Ruff и mypy проходят.
- [x] Runtime HTTP check возвращает ожидаемые коды и payload.

**Dependencies:** Task 0.1

**Estimated scope:** Medium (3–5 files)

## Task 0.3: Добавить локальную инфраструктуру

**Acceptance criteria:**
- [x] Compose описывает PostgreSQL/pgvector, Redis, MinIO и legal-core.
- [x] Сервисы имеют healthchecks, persistent volumes и environment placeholders.
- [x] Секреты не захардкожены; `.env` исключён из Git.

**Verification:**
- [x] `docker compose config --quiet` проходит.
- [x] Контейнеры стартуют и readiness становится healthy.

**Dependencies:** Task 0.2

**Estimated scope:** Medium (3–5 files)

## Checkpoint: Bootstrap slice

- [x] Unit/contract tests, lint и typecheck проходят.
- [x] Compose config и runtime health проверены.
- [x] Git diff проверен на scope и секреты.

## Task 0.4: Подключить Telegram gateway в безопасном режиме

**Acceptance criteria:**
- [x] Токен получается только из environment и не попадает в Git/логи.
- [x] `/start` и `/help` явно сообщают об ограничениях технического режима.
- [x] Приветствие, аватар, описания и inline-меню оформлены в едином стиле.
- [x] Свободный текст не обрабатывается до появления auth/Case Core.
- [x] Gateway запускается без host-портов и от непривилегированного пользователя.

**Verification:**
- [x] Unit tests, Ruff и mypy проходят.
- [x] Telegram API `getMe`, регистрация команд и container health проверены.

**Dependencies:** Task 0.3

## Task 0.5: Добавить CI и закрепить Hermes

**Acceptance criteria:**
- [ ] CI запускает pytest, Ruff, mypy, lock/install и Compose validation.
- [ ] Hermes подключён по проверенному immutable release/commit, а не по `main`.
- [ ] Compatibility smoke test доказывает, что Hermes не обходит Legal Core/MCP boundaries.

**Verification:**
- [ ] CI проходит из чистого checkout.
- [ ] Upstream reference и rationale записаны в ADR.

**Dependencies:** Task 0.4; human review для выбора Hermes pin

## Following: Task 1.1 — identity/tenant data contract

## Task 1.1: Administrator intake and persistence contract

**Acceptance criteria:**
- [x] Intake, missing-facts and report contracts are fixed in a versioned specification.
- [x] Canonical report and legal-corpus lifecycle decisions are recorded in ADRs.
- [x] Alembic creates identity, case, report, audit and legal-corpus tables.
- [x] Tenant identity is resolved by Legal Core and cannot be supplied in a request body.
- [x] Cross-tenant and idempotency contract tests pass.

**Dependencies:** Task 0.4

## Task 1.2: Case Core and blocked intake report

**Acceptance criteria:**
- [x] Case/fact/finalisation endpoints implement the stable error envelope.
- [x] Critical missing facts deterministically produce the next question.
- [x] One canonical JSON creates both the Telegram summary and PDF.
- [x] Legal sections remain explicitly unavailable before evidence gates.

**Dependencies:** Task 1.1

## Task 2.1: First verified legal corpus and retrieval

**Acceptance criteria:**
- [x] Official raw artifacts and metadata are ingested reproducibly with SHA-256.
- [x] Approval is separate from ingestion and audited.
- [x] Retrieval exposes only approved versions applicable on `as_of_date`.
- [x] The 2026-09-01 Decree 736/659 boundary is covered by corpus regression tests.

**Dependencies:** Task 1.1

## Task 2.2: Telegram administrator workflow

**Acceptance criteria:**
- [x] `Создать кейс` is available only to a mapped `CLINIC_ADMIN`.
- [x] The bot collects only the minimum pseudonymous intake data in steps.
- [x] Restart/cancel/retry do not duplicate cases or facts.
- [x] `/whoami` gives the administrator the identifier needed for secure bootstrap.

**Dependencies:** Tasks 1.2 and 2.1

## Task 2.2a: Subscription-gated clinic access

**Acceptance criteria:**
- [x] A `CLINIC_ADMIN` can use protected Legal Core and Telegram intake only with an active,
  time-valid entitlement for that same clinic.
- [x] Suspended, cancelled and expired entitlements produce the stable
  `SUBSCRIPTION_INACTIVE` error and reveal no case data.
- [x] Entitlements and their append-only audit events are tenant-scoped, RLS-protected and
  created by an Alembic migration.
- [x] Internal provisioning cannot change a subscriber into `LEGAL_EDITOR`.
- [x] Payment data, acquirer credentials and payment webhooks are not stored or introduced.
- [x] The configured platform owner can grant `MVP_MANUAL` access by Telegram ID through the bot;
  the server checks ownership, creates an isolated first clinic when required and keeps the action
  idempotent.

**Verification:**
- [x] PostgreSQL API tests cover active, missing, suspended and expired access plus tenant scope.
- [x] Telegram wizard explains inactive access without exposing internal subscription details.
- [x] API and bot tests reject non-owner grants and validate owner grant/replay behaviour.

**Dependencies:** Task 1.1

## Evidence gate

Recommendations, legal risk conclusions and patient-response drafts remain disabled until
approved-only retrieval, applicable-date resolution and claim-to-evidence verification pass.

## Task 2.3: Human legal review of the initial corpus

**Acceptance criteria:**
- [ ] An active platform-side `LEGAL_EDITOR` can inspect the bounded candidate list, immutable
  official artifact, effective dates, checksums and selected fragments in Telegram; clinic roles,
  subscription and platform-owner ID do not substitute this server-side role check.
- [ ] Only a qualified `LEGAL_EDITOR` can make all four explicit attestations and submit a
  checksum-bound, idempotent approval through Legal Core; stale or legacy candidates fail closed.
- [ ] A qualified, platform-side `LEGAL_EDITOR` has reviewed the immutable official artifacts using
  `docs/legal-review/initial-corpus-review.md`; no version is approved by this task automatically.
- [ ] The PP №659 fragment selection covers the intended recommendation scenarios before approval.
- [ ] Every approved version has a checksum-bound, append-only approval attestation.

**Dependencies:** Task 2.1; `SPEC-legal-review-workspace.md`; explicit human legal review

## Task 3.0: Approve the evidence/risk/agent release packet

**Acceptance criteria:**
- [ ] A qualified platform-side `LEGAL_EDITOR` approves legal-base v1 scope and every
  checksum-bound artifact/selection; subscriber and platform owner cannot substitute approval.
- [ ] Product owner approves covered incidents plus monetary-threshold semantics.
- [ ] Legal editor approves `risk-policy.v1` triggers and regression scenarios.
- [ ] Security/product owner approves provider/data processing and an immutable Hermes revision,
  or explicitly keeps the provider disabled.

**Dependencies:** Task 2.3

**Reference:** `SPEC-evidence-risk-agents-updater.md`

## Task 3.1: Versioned deterministic risk and escalation

**Acceptance criteria:**
- [ ] Alembic adds immutable risk-policy, case-risk and escalation records with tenant/RLS scope.
- [ ] Typed facts produce `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` or `UNAVAILABLE` with reason codes,
  policy version and input snapshot hash.
- [ ] Missing policy-required facts and absent policy fail closed; CRITICAL blocks external draft.
- [ ] A policy change makes a new version and cannot rewrite an earlier report.

**Dependencies:** Task 3.0

## Task 3.2: Evidence claims and verifier gate

**Acceptance criteria:**
- [ ] Every legal/action claim has date-applicable approved evidence, source metadata and a
  verifier result.
- [ ] Unsupported, contradictory, inapplicable or incomplete claims block recommendations and
  patient-response drafts deterministically.
- [ ] Contract, tenant-negative, expired-version and no-auto-send tests pass.

**Dependencies:** Tasks 2.3, 3.1

## Task 3.3: Pinned, least-privilege agent integration

**Acceptance criteria:**
- [ ] Hermes is pinned to an approved immutable revision and uses only read-only,
  server-authorised contracts.
- [ ] Provider adapter is disabled unless a provider/data-processing decision is approved.
- [ ] Pseudonymisation, timeout, audit-redaction, authz and bypass-negative tests pass.

**Dependencies:** Tasks 3.0, 3.2

## Task 4.1: Deterministic legal updater and comparison review queue

**Progress note (2026-09-10):**
- [x] Production watcher sends requests to `publication.pravo.gov.ru` only through the existing
  internal VPN proxy; an unset, malformed or non-allowlisted proxy fails closed and cannot divert
  official-law traffic to an arbitrary endpoint.

**Acceptance criteria:**
- [ ] Versioned source allowlist, immutable fetch, SHA-256 idempotency, parse and structural diff
  produce `REVIEW_REQUIRED` candidates only.
- [ ] Automatic promotion is impossible; reviewer attestation and regression are required.
- [ ] Fetch/parser/source-boundary failures are auditable and fail closed.

**Dependencies:** Tasks 2.3, 3.0

## Task 5.1: Controlled free-pilot entitlement

**Acceptance criteria:**
- [x] Product owner approves owner-granted, time-limited `FREE_PILOT` access rather than open
  public self-registration.
- [x] The existing user+clinic entitlement guard applies equally to free pilot access and cannot
  bypass evidence/risk/verifier gates.
- [x] Payment data and new user identity categories remain absent.

**Dependencies:** `SPEC-free-pilot-practice-research.md`; product/security approval

## Task 5.2: Licensed and reviewed practical-scenario regression library

**Progress note (2026-09-04):**
- [x] Public-topic discovery is recorded in
  `docs/legal-review/practical-scenario-discovery-2026-09-04.md` without copying posts,
  treating them as legal evidence, or adding them to the corpus/training set.
- [ ] Convert only licensed, de-identified and independently reviewed candidates into executable
  regression fixtures.

**Acceptance criteria:**
- [ ] Each scenario has documented provenance/rights, de-identification, two-person legal review,
  official-source links, expected actions and expiry review date.
- [ ] Public forum text is not scraped, stored as a corpus, used as production evidence or used
  for training without written permission/licence.
- [ ] P0 scenario failures block corpus/policy promotion; fixtures contain no real patient data.

**Dependencies:** Task 3.0; legal/security approval

## Task 1.3: Durable Telegram intake drafts

**Acceptance criteria:**
- [x] Legal Core stores multiple active, pseudonymous drafts per administrator and clinic with RLS,
  optimistic revision and an Alembic migration.
- [x] Create/list/read/update/archive contracts use server-side actor scope, idempotency and
  tenant-negative tests; list entries contain no free-text patient facts.
- [x] Telegram persists each accepted transition, presents **Мои черновики**, supports switching
  and restarts from the exact next question.
- [x] Leaving to the menu preserves the draft; explicit archive and completed submission remove it
  from active drafts without creating duplicate cases or reports.

**Verification:**
- [x] Unit/contract suite, Ruff, mypy and Compose validation pass.
- [x] Isolated PostgreSQL migration/API suite proves RLS ownership, revision conflict and 30-day
  purge; production deployment completed on 2026-08-31.

**Dependencies:** Tasks 1.1, 2.2a; `SPEC-durable-telegram-drafts.md`

## Task 0.6: Controlled GitHub-to-VPS deployment

**Acceptance criteria:**
- [x] Every pull request and `main` update runs Ruff, mypy, pytest, Compose validation, PostgreSQL/MinIO integration tests, dependency audit and CodeQL.
- [x] A successful `main` build may deploy only through the protected `production` environment and a key-only, forced-command server account.
- [x] Production runtime secrets remain only in a root-owned server file and are absent from Git, workflow logs and GitHub repository variables.
- [x] Deploy validates that the requested commit is reachable from `origin/main`, runs the base Compose profile and proves `/health/ready`.
- [x] Rollback is an explicit production workflow and deployment steps are documented.

**Verification:**
- [x] Workflow files pass static validation and the full local quality bar passes.
- [x] Server Docker/Compose installation and restricted deployment account are verified without enabling the application.
- [ ] A commit after runtime configuration reaches a healthy base-profile deployment; Hermes and maintenance profiles remain disabled.

**Dependencies:** Task 0.5; explicit human provision of Telegram production configuration

## Task 6.1: Six-point bot reliability remediation

**Acceptance and local evidence:**
- [x] Human-approved verified copies reach production retrieval; a real Core/orchestrator/worker
  HTTP integration test produces an evidence-linked LOW report and persisted PDF after approval.
- [x] Pending/date-inapplicable evidence blocks recommendations; software does not approve laws.
- [x] Analysis intent survives app recreation; workers use exclusive expiring leases and reconcile
  committed responses; duplicate callbacks do not create concurrent work for a case.
- [x] Versioned opt-in early triage creates HIGH/CRITICAL cards with unavailable evidence/model;
  v1 behavior stays unchanged; deterministic routing does not authorize legal recommendations.
- [x] Actual PTB dispatcher tests isolate wizard/quick/admin/discussion modes and old buttons.
- [x] Lawyer card, claim, answer, resolution, chronological history and pagination have role,
  tenant, concurrency, retention and immutable-audit regression tests.
- [x] Editor can download full source PDF and complete selected excerpts; list queries avoid
  pulling PDF/full-text columns or running approval preflight on every row.
- [x] Independent security review findings were reproduced, fixed and rechecked by root.
- [x] Clean local PostgreSQL/MinIO suite: 503 passed; all-three-package mypy, Ruff, Compose and
  dependency audit passed. Final file-delivery closeout requires one more integrated run.
- [ ] GitHub CI passes on the final release commit; human review/merge approval recorded.
- [ ] Deploy via existing main workflow and verify production service, migrations and queue.
- [ ] Owner authorizes and activates the new early-triage policy, retaining the 50,000 RUB threshold.
- [ ] Lawyers manually approve applicable legal versions; owner checks private-chat user flow.

**Dependencies:** `SPEC-bot-reliability.md`; ADR0021/0023/0024/0026/0029/0030.

## Task 6.2: Integrated launch audit (2026-09-15)

- [x] Integrate PR #42 without dropping durable analysis, lawyer workspace or reviewed-copy retrieval.
- [x] Restore private Telegram delivery, durable input transitions and complete long reports.
- [x] Persist model abstention, reject unknown/invalid evidence dependencies and uncertain dates.
- [x] Validate fenced job headers before invoking models; preserve committed results on timeout.
- [x] Prevent starvation and concurrent replacement in official-source updates; provide hash-locked PDF recovery.
- [x] Fix Alembic password escaping and provide persistent opt-in full Hermes deployment.
- [x] First integrated GitHub run passes: 500 general tests and 301 Core/PostgreSQL/MinIO tests
  on `ddd0da92a3e5af0eb1c2837945a8488a6c557778`, including the synthetic HTTP end-to-end scenario.
- [x] Final CI passes after the fixed MinIO source-build update and official PDF verification
  (PR run 34994520047; main quality and integration jobs in run 34996415369).
- [ ] Production retry after SSH disconnected during the initial MinIO source build;
  keepalive and smaller build concurrency are being validated. OOM is not confirmed.
- [ ] Deployment host smoke with actual Telegram/Hermes settings and human-approved legal versions.

**Evidence:** `docs/launch-audit-2026-09-15.md`; PR #43.
