# ADR-0048: Make Legal Core readiness verify usable dependencies

- **Status:** Accepted
- **Date:** 2026-09-30

## Context

The production readiness endpoint previously treated a successful TCP connection
to PostgreSQL, Redis and object storage as sufficient. A reachable port does not
prove that Legal Core can authenticate, execute its runtime role, or use the
storage bucket. Such a false-positive readiness result can send Telegram traffic
to a Core that will immediately fail its first case operation.

`tests/test_personal_clinic_boundary.py` deliberately freezes the clinic startup
surface while the hidden personal-preview work is isolated. This operational
change touches that startup file and therefore requires an explicit reviewed
baseline update rather than weakening or removing the guard.

## Decision

The readiness probe uses the same runtime configuration and capabilities as the
application:

- PostgreSQL: open the runtime session factory and run a bounded `SELECT 1`;
- Redis: establish a bounded connection and require a `PONG` to a `PING`;
- object storage: perform an authenticated, bounded bucket `HEAD` through the
  configured store. A missing bucket remains ready because first use creates it;
  authentication and other storage failures do not.

The endpoint returns not-ready when any probe fails. Liveness stays process-only,
so a supervisor can distinguish a dependency outage from a dead process. No
patient content, credentials, or storage object names are emitted by these
probes.

The clinic-boundary test retains its startup-file hash guard and records the
reviewed new blob identifier. This ADR is the separate clinic-interface review
required for that single baseline update. It does not expose or mount any
personal-preview route, alter tenant authorization, legal approval, or case-data
collection.

## Consequences

Deployments wait for dependencies that are actually usable by Legal Core rather
than merely reachable. A transient dependency outage may make readiness fail;
that is intentional fail-closed behaviour. Tests cover each successful runtime
probe and the aggregate health response alongside the existing personal/clinic
isolation checks.

## Alternatives considered

- Keep TCP-only checks: rejected because they mask invalid credentials, broken
  runtime grants and non-working object-storage access.
- Perform case reads or write a test object: rejected because readiness must not
  access tenant material or mutate production state.
- Remove the personal-boundary hash test: rejected; the guard continues to make
  future startup-surface changes explicit.
