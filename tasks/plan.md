# Implementation Plan: Dental Legal AI MVP

## Overview

Реализация следует ТЗ v0.1 и карте `CAPABILITY_MAP.md`. Работа идёт вертикальными срезами: каждый срез оставляет систему запускаемой и проверяемой. LLM/Telegram не подключаются, пока Legal Core, tenant isolation и детерминированные safety-контракты не доказаны тестами.

## Architecture Decisions

- Legal Core — единственный источник нормативной истины; см. ADR-0001.
- Tenant context принадлежит серверу, а не клиенту/LLM; см. ADR-0002.
- Модульный монорепозиторий используется для MVP, но компоненты общаются через явные REST/MCP contracts.
- PostgreSQL — system of record; Redis не хранит единственную копию юридически значимого состояния.
- Legal updater создаёт immutable versions и не индексирует их до approval/regression gate.

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
