# Dental Legal AI

> ⚠️ This repository is an early technical foundation for a dental legal-assistant platform. It is not a lawyer, does not provide final legal conclusions, and must not be used with real patient data until the production privacy/security review is complete.

The system combines a tenant-isolated Legal Core, Telegram intake, versioned official-law corpus, deterministic risk rules, audited approvals, Hermes-based agent orchestration and clinic-owned document context.

## Current scope

Implemented building blocks include:

- FastAPI Legal Core with health/readiness checks;
- Telegram administrator workflow, durable drafts and local one-message quick intake without LLM;
- tenant identity, subscription gating and RLS;
- versioned official legal corpus with human approval gates;
- exact/FTS/pgvector legal retrieval;
- deterministic risk assessment and escalation;
- semantic claim verification;
- pinned Hermes agent orchestration with pseudonymisation;
- deterministic legal-source watcher/review queue;
- tenant-owned Clinic Documents with immutable versions, approval states, MinIO raw storage and Telegram upload/library flows;
- metadata-only public dental-clinic reference registry for development taxonomy research;
- fully synthetic Clinic Documents fixture pack for parser, time-travel and retrieval regression tests.

See `docs/`, `SPEC-*.md`, `tasks/` and `AGENTS.md` for architecture and safety rules. Before any
closed pilot, follow [the operational readiness checklist](docs/closed-pilot-readiness.md).
