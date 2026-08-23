# Capability Map: Dental Legal AI MVP

Карта декомпозирует ТЗ v0.1 на независимо проверяемые модули. Идентификаторы стабильны и используются в спецификациях и задачах.

| Module id | Ответственность | Зависит от |
|---|---|---|
| `platform-bootstrap` | Репозиторий, конфигурация, контейнеры, health/readiness, базовые quality gates | — |
| `identity-tenancy` | Clinics, users, RBAC, server-side tenant context, tenant isolation | `platform-bootstrap` |
| `subscription-access` | Индивидуальные права платного доступа к конкретной клинике, сроки и server-side enforcement | `identity-tenancy` |
| `case-core` | Cases, messages, typed facts, missing facts, audit state transitions | `identity-tenancy` |
| `legal-corpus` | Trusted sources, documents, immutable versions, effective-date resolver, approval lifecycle | `platform-bootstrap`, `identity-tenancy` |
| `legal-retrieval` | Exact/FTS/vector retrieval, metadata/date filters, evidence contracts | `legal-corpus` |
| `risk-escalation` | Deterministic score, reason codes, versioned policy, lawyer escalation | `case-core` |
| `agent-orchestration` | Pinned Hermes, Case/Research/Verifier agents, pseudonymized bounded context | `case-core`, `legal-retrieval`, `risk-escalation` |
| `legal-updater` | Deterministic discover/fetch/parse/version/diff/review/regression promotion pipeline | `legal-corpus`, `legal-retrieval` |
| `telegram-gateway` | Telegram auth mapping, dialogue, draft-only response cards | `identity-tenancy`, `case-core`, `agent-orchestration` |
| `pilot-operations` | Metrics, backups/restore, security review, regression corpus, pilot controls | все поставляемые модули |

Порядок сборки:

`platform-bootstrap` → (`identity-tenancy`, `legal-corpus`) → `subscription-access` → `case-core` → (`legal-retrieval`, `risk-escalation`) → `agent-orchestration` → (`telegram-gateway`, `legal-updater`) → `pilot-operations`.

Ключевой принцип зависимости: Hermes и Telegram потребляют Legal Core через ограниченные API/MCP-контракты; они не владеют нормативными текстами, risk policy или tenant identity.
