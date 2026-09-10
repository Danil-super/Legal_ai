# Production deployment

This directory configures the base (non-AI) deployment on `84.201.153.147`.

## Trust boundaries

- GitHub `main` is the only application source. The VPS validates the requested revision is in
  `origin/main` before deployment.
- GitHub holds only the `production` environment secret `DEPLOY_SSH_PRIVATE_KEY`.
- The VPS holds a separate root-owned, read-only GitHub deploy key. It can fetch this repository
  but cannot push or access other repositories.
- `/etc/dental-legal-ai/app.env` is root-owned and contains all runtime secrets. It is never
  copied to GitHub, CI artifacts or logs.
- The Telegram VLESS configuration is stored only in the root-owned
  `/etc/dental-legal-ai/telegram-vpn/config.json` (mode `0600`). It is never committed or placed
  in `app.env`; the gateway reaches Telegram exclusively through this internal proxy.
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

`Rollback production` is a manual GitHub Actions workflow. It accepts a full commit SHA that is
still reachable from `main`. Do not roll back across a non-backward-compatible Alembic migration;
restore from a tested backup instead.
