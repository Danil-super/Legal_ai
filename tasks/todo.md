# Dental Legal AI task list

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
- [ ] A qualified, platform-side `LEGAL_EDITOR` has reviewed the immutable official artifacts using
  `docs/legal-review/initial-corpus-review.md`.
- [ ] The PP №659 fragment selection covers the intended recommendation scenarios before approval.
- [ ] Every approved version has a checksum-bound, append-only approval attestation.

**Dependencies:** Task 2.1; explicit human legal review

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
