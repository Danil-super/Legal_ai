# ADR-0051: Isolate pinned MinIO security builds from ordinary application deployments

- **Status:** Accepted
- **Date:** 2026-10-01

## Context

The 2 GiB production VPS has a pinned MinIO security release that upstream
requires consumers to build from source. Running `docker compose up --build`
for every application revision recompiles that release alongside the live bot.
It lengthens a routine deploy substantially and creates avoidable memory
pressure without adding any new verification of the already pinned image.

## Decision

The deployment script verifies the exact local MinIO image tag before it builds
only the mutable application services and starts Compose with `--no-build`. A
missing pinned image stops before containers are replaced. A changed MinIO
revision remains a separate reviewed maintenance operation: build the source
release, verify object-storage behaviour and backups, then permit the related
application deployment.

CI continues to build the same pinned source release and run the real
PostgreSQL/MinIO integration tests for every main revision. The runtime does
not fall back to an old public image or an arbitrary registry image.

## Consequences

Normal Telegram and Legal Core changes no longer recompile MinIO on the VPS.
The source-controlled deployment contract, its test, and the installed
root-owned deploy script must be updated together when the pinned MinIO
revision changes.

## Alternatives considered

- Keep `up --build`: rejected because it recompiles an unchanged security image
  and can exhaust the constrained host during routine releases.
- Reuse an unpinned public image: rejected because it weakens provenance and
  could restore a known-vulnerable release.
- Automatically build a new MinIO revision inside the application deploy:
  rejected because storage upgrades require an explicit backup and
  compatibility check.
