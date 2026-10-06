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
- Telegram and the official-publication watcher use direct TLS egress by default. The gateway
  joins the Compose `edge` network only in addition to its private backend connection; no service
  or port is published to the Internet. A VPN is not a production prerequisite.
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

The deployment script builds only application images and starts Compose with `--no-build`. It
first verifies that the exact pinned MinIO security image is already local. When the MinIO
revision changes, build that image in a planned maintenance window before enabling the related
application deployment; this prevents every ordinary bot change from recompiling MinIO on the
2 GiB host.

`Rollback production` is a manual GitHub Actions workflow. It accepts a full commit SHA that is
still reachable from `main`. Do not roll back across a non-backward-compatible Alembic migration;
restore from a tested backup instead.
