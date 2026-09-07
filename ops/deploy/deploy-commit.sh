#!/usr/bin/env bash
# Installed on the VPS as /usr/local/sbin/dental-legal-ai-deploy.
# It is root-owned and intentionally updated only through an explicit server-administration step.
set -euo pipefail

readonly repository_dir="/srv/dental-legal-ai/repository"
readonly env_file="/etc/dental-legal-ai/app.env"
readonly state_dir="/var/lib/dental-legal-ai"
readonly project_name="dental-legal-ai"
readonly readiness_url="http://127.0.0.1:8000/health/ready"
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
install -d -m 0750 "$state_dir"
install -d -m 0755 "$(dirname "$lock_file")"

exec 9>"$lock_file"
if ! flock -n 9; then
  echo "Another deployment is already running." >&2
  exit 75
fi

if [[ "$(git -C "$repository_dir" config --get remote.origin.url)" != "https://github.com/Danil-super/Legal_ai.git" ]]; then
  echo "Unexpected deployment repository origin." >&2
  exit 65
fi

git -C "$repository_dir" fetch --prune origin "+refs/heads/main:refs/remotes/origin/main"
if ! git -C "$repository_dir" merge-base --is-ancestor "$revision" origin/main; then
  echo "Requested revision is not reachable from origin/main." >&2
  exit 65
fi

git -C "$repository_dir" checkout --detach --force "$revision"
cd "$repository_dir"

docker compose --project-name "$project_name" --env-file "$env_file" \
  -f docker-compose.yml -f ops/deploy/docker-compose.production.yml \
  up --build --detach --remove-orphans

for _ in $(seq 1 30); do
  if curl --fail --silent --show-error "$readiness_url" >/dev/null; then
    printf '%s\n' "$revision" >"$state_dir/last-successful-revision"
    logger --tag dental-legal-ai-deploy "${operation} succeeded for ${revision}"
    exit 0
  fi
  sleep 2
done

logger --tag dental-legal-ai-deploy "${operation} failed readiness for ${revision}"
echo "Legal Core did not become ready; the last successful revision was preserved for manual rollback." >&2
exit 1
