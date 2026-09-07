# ADR-0014: Deploy approved `main` commits through a restricted GitHub-to-VPS path

## Status

Accepted

## Context

Dental Legal AI needs repeatable deployment from `Danil-super/Legal_ai` to a single Ubuntu VPS.
The runtime handles clinic data, so deployment credentials and application credentials must not
be mixed. The VPS currently has no container runtime and no staging environment. The base product
does not require a public HTTP endpoint: Telegram uses outbound long polling and Legal Core is
bound to loopback only.

## Decision

- GitHub Actions runs quality, integration, dependency-audit and CodeQL gates on pull requests
  and `main`.
- Only a successful push to `main` can run the production deployment job. It uses GitHub's
  `production` environment and an SSH private key stored as an environment secret.
- The server hosts a dedicated `deploy` account whose key is restricted to a forced command.
  That command accepts only `deploy <40-hex-commit>` or `rollback <40-hex-commit>` and invokes a
  root-owned deployment program through a narrow sudo rule.
- The root-owned program fetches `origin/main`, rejects a commit outside that ancestry, checks out
  the requested immutable revision, then starts only the default Compose profile and requires
  Legal Core readiness. A failed readiness check is reported to GitHub; it does not perform an
  implicit database downgrade.
- `/etc/dental-legal-ai/app.env` is owned by root and contains runtime configuration. GitHub
  receives only the deploy SSH key, never application secrets.
- `DEPLOY_ENABLED` is a protected GitHub environment variable and defaults to `false` until the
  platform owner configures the genuine Telegram token and identifiers on the host.

## Consequences

- A compromised application secret does not provide GitHub or shell deployment access, and a
  GitHub Actions secret does not disclose patient-data credentials.
- Deploy is reproducible from a Git commit and is blocked when the checkout source is not `main`.
- Docker builds execute trusted `main` code as part of the deployment boundary; branch protection
  and required CI checks are therefore mandatory operational controls.
- Rollbacks must be considered together with Alembic migration compatibility and backup/restore.
  Future destructive schema changes need an expand/contract migration plan.
- This ADR provisions only the deterministic base profile. Hermes, external LLM use, legal watcher
  and automatic patient-facing communications remain disabled.
