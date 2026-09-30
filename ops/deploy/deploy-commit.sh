#!/usr/bin/env bash
# Installed on the VPS as /usr/local/sbin/dental-legal-ai-deploy.
# It is root-owned and intentionally updated only through an explicit server-administration step.
set -euo pipefail

readonly repository_url="git@github.com:Danil-super/Legal_ai.git"
readonly repository_dir="/srv/dental-legal-ai/repository"
readonly env_file="/etc/dental-legal-ai/app.env"
readonly state_dir="/var/lib/dental-legal-ai"
readonly github_deploy_key="/etc/dental-legal-ai/github-deploy-readonly"
readonly github_known_hosts="/etc/dental-legal-ai/github_known_hosts"
readonly git_ssh_command="ssh -i ${github_deploy_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${github_known_hosts}"
readonly project_name="dental-legal-ai"
readonly lock_file="/run/lock/dental-legal-ai-deploy.lock"

if [[ "${EUID}" -ne 0 ]]; then
  echo "This program must run as root." >&2
  exit 77
fi

if [[ "$#" -ne 2 || ! "$1" =~ ^(deploy|rollback)$ || ! "$2" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: dental-legal-ai-deploy <deploy|rollback> <40-hex-commit>." >&2
  exit 64
fi

readonly operation="$1"
readonly revision="$2"

test -f "$env_file"
test -d "$repository_dir/.git"
test -r "$github_deploy_key"
test -r "$github_known_hosts"
install -d -m 0750 "$state_dir"
install -d -m 0755 "$(dirname "$lock_file")"

exec 9>"$lock_file"
if ! flock -n 9; then
  echo "Another deployment is already running." >&2
  exit 75
fi

if [[ "$(git -C "$repository_dir" config --get remote.origin.url)" != "$repository_url" ]]; then
  echo "Unexpected deployment repository origin." >&2
  exit 65
fi

GIT_SSH_COMMAND="$git_ssh_command" git -C "$repository_dir" fetch --prune origin "+refs/heads/main:refs/remotes/origin/main"
if ! git -C "$repository_dir" merge-base --is-ancestor "$revision" origin/main; then
  echo "Requested revision is not reachable from origin/main." >&2
  exit 65
fi

git -C "$repository_dir" checkout --detach --force "$revision"
cd "$repository_dir"

# Select the persistent server mode. Never source the secrets file as shell code.
# This deployment flag deliberately accepts only a literal 0 or 1.
analysis_enabled=0
analysis_flag_seen=0
while IFS= read -r env_line || [[ -n "$env_line" ]]; do
  if [[ "$env_line" =~ ^[[:space:]]*(export[[:space:]]+)?DEPLOY_ANALYSIS_ENABLED[[:space:]]*=(.*)$ ]]; then
    if [[ "$analysis_flag_seen" -eq 1 || ! "${BASH_REMATCH[2]}" =~ ^[[:space:]]*([01])[[:space:]]*(#.*)?$ ]]; then
      echo "DEPLOY_ANALYSIS_ENABLED must occur once and contain a literal 0 or 1." >&2
      exit 64
    fi
    analysis_enabled="${BASH_REMATCH[1]}"
    analysis_flag_seen=1
  fi
done <"$env_file"

# BuildKit on the constrained VPS rejects concurrent Compose build sessions.
# Keep service builds serial so a deploy cannot strand a partially replaced stack.
compose_args=(--parallel 1 --project-name "$project_name" --env-file "$env_file"
  -f docker-compose.yml -f ops/deploy/docker-compose.production.yml)
if [[ "$analysis_enabled" == 1 ]]; then
  compose_args+=(-f ops/hermes/docker-compose.hermes.yml --profile analysis)
fi

# Validate before replacing containers. Suppress Compose interpolation diagnostics,
# which can include malformed secret values; report only a bounded operator action.
if ! docker compose "${compose_args[@]}" config --quiet >/dev/null 2>&1; then
  echo "Compose configuration is invalid. Check app.env and the selected deployment overlays." >&2
  exit 1
fi
if [[ "$analysis_enabled" == 1 ]]; then
  if ! docker compose "${compose_args[@]}" config --format json 2>/dev/null \
    | python3 ops/deploy/analysis-preflight.py; then
    exit 1
  fi
fi

readonly minio_image="dental-legal-minio:9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a"
# This image is a security boundary, so ordinary application deployments may
# reuse it only after confirming that the exact pinned release is local.  It is
# intentionally built as a separate, planned maintenance action when its
# upstream revision changes; `up --build` would otherwise recompile MinIO on
# every small bot change and can exhaust a constrained VPS.
if ! docker image inspect "$minio_image" >/dev/null 2>&1; then
  echo "The required pinned MinIO security image is missing. Build the reviewed release before deployment." >&2
  exit 1
fi

build_services=(legal-core legal-watcher legal-watch-importer telegram-gateway)
if [[ "$analysis_enabled" == 1 ]]; then
  build_services+=(agent-orchestrator)
fi

if ! docker compose "${compose_args[@]}" build "${build_services[@]}"; then
  echo "Application image build failed; the running stack was not replaced." >&2
  exit 1
fi

# Legal Core alone can be healthy while Telegram fails authentication or cannot poll.
# Compose checks every enabled service, including the gateway's readiness marker;
# the Core healthcheck runs inside the container and honors any host port mapping.
if ! docker compose "${compose_args[@]}" \
  up --no-build --detach --remove-orphans --wait --wait-timeout 180; then
  logger --tag dental-legal-ai-deploy "${operation} failed readiness for ${revision}"
  echo "The application stack did not become ready; the last successful revision was preserved for manual rollback." >&2
  exit 1
fi

printf '%s\n' "$revision" >"$state_dir/last-successful-revision"
logger --tag dental-legal-ai-deploy "${operation} succeeded for ${revision}"
