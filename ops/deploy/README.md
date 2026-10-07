# Production deployment

This directory configures the base (non-AI) deployment on `89.23.108.254`.

## Trust boundaries

- GitHub `main` is the only application source. The VPS validates the requested revision is in
  `origin/main` before deployment.
- GitHub holds only the `production` environment secret `DEPLOY_SSH_PRIVATE_KEY`.
- The VPS holds a separate root-owned, read-only GitHub deploy key. It can fetch this repository
  but cannot push or access other repositories.
- The VPS reaches GitHub SSH through its documented port 443 endpoint. The pinned GitHub host key
  remains verified before every fetch.
- `/etc/dental-legal-ai/app.env` is root-owned and contains all runtime secrets. It is never
  copied to GitHub, CI artifacts or logs.
- Telegram uses direct TLS egress by default. The gateway joins the Compose `edge` network only
  in addition to its private backend connection; no service or port is published to the Internet.
  A VPN is not a production prerequisite for the bot.
- The official-publication watcher and its importer are an `official-watch` maintenance profile,
  not part of the routine deployment. They may be started only after their source route has been
  independently verified. They can stage only `REVIEW_REQUIRED` candidates and never approve a
  legal version.
- An old VLESS profile, if retained on a host, stays root-owned and inactive; it is not read by
  the normal deployment and must never be committed or copied to GitHub.
- The `deploy` SSH account has no shell access: its key is forced to the deployment gateway.
- The server's ED25519 host key is pinned in `known_hosts.production`; a host key change blocks
  the workflow until manually reviewed.

## Bootstrap

From a reviewed checkout, generate two separate Ed25519 keys: one dedicated to GitHub Actions
deployment and one read-only key for the VPS to fetch this private repository. Add the latter's
public half in **Repository settings → Deploy keys** without write access. Upload the former's
private half as the GitHub `production` environment secret `DEPLOY_SSH_PRIVATE_KEY`, then run the
bootstrap script as root with its public half and the local path to the VPS fetch-key private half.
Do not reuse administrator or other-server keys.

The script installs Docker using Docker's official APT repository, installs the restricted deploy
path, pins GitHub's SSH host keys, clones the private GitHub repository to
`/srv/dental-legal-ai/repository`, and creates the root-owned environment template. It does not
start the bot and does not alter firewall rules.

Before enabling deployment, replace every placeholder in `/etc/dental-legal-ai/app.env` with
distinct generated passwords and the genuine Telegram token/owner identifiers. Keep
`DEPLOY_ENABLED=false` in GitHub until this is complete.

## Deployment and rollback

The `Deploy production` job runs only after all CI jobs succeed for a `main` commit and only if
the GitHub `production` environment variable `DEPLOY_ENABLED` is `true`. It starts the default
Compose profile with conservative memory limits for the 2 GiB VPS and then requires
`http://127.0.0.1:8000/health/ready`.

The deployment script builds only images needed by the default application profile and starts
Compose with `--no-build`. It first verifies that the exact pinned MinIO security image is already
local. Build the `official-watch` profile separately in a reviewed maintenance operation after
its egress is verified. When the MinIO
revision changes, build that image in a planned maintenance window before enabling the related
application deployment; this prevents every ordinary bot change from recompiling MinIO on the
2 GiB host.

`Rollback production` is a manual GitHub Actions workflow. It accepts a full commit SHA that is
still reachable from `main`. Do not roll back across a non-backward-compatible Alembic migration;
restore from a tested backup instead.

Normal deployments also compare the request with the last successful revision
before checkout or building. A delayed request for a strict ancestor is a successful
no-op; the newer stack and success state are left untouched. Same-SHA requests
still rebuild, so reviewed `app.env` changes (including the analysis profile) can
be applied. Missing state allows initial bootstrap; malformed/non-commit state
or incomparable histories fail closed. Explicit rollback retains its intentional
operator-controlled downgrade semantics and still validates `origin/main` ancestry.
See [ADR-0067](../../docs/adr/0067-monotonic-normal-deployment-revisions.md).

### Updating the installed deployer

The root-owned `/usr/local/sbin/dental-legal-ai-deploy` does **not** update when an
application commit is checked out. After independent review and green CI, an
administrator must separately verify an immutable trusted checkout and install
the reviewed script. On the VPS, from that verified checkout, run as root:

```bash
bash -n ops/deploy/deploy-commit.sh
install -o root -g root -m 0750 ops/deploy/deploy-commit.sh /usr/local/sbin/dental-legal-ai-deploy
cmp ops/deploy/deploy-commit.sh /usr/local/sbin/dental-legal-ai-deploy
```

Perform this administration step between deployments under the existing
`/run/lock/dental-legal-ai-deploy.lock` (for example, in an interactive root shell
with `exec 9>/run/lock/dental-legal-ai-deploy.lock` followed by `flock -n 9`; stop
if acquiring it fails). Retain the prior installed script for recovery. Do not
change deploy keys, the forced command, sudo rules or `app.env` during this update.
Do not erase or hand-edit success state to bypass the guard. Recheck service
readiness after the next authorized deploy; do not assume a merge installed this fix.
