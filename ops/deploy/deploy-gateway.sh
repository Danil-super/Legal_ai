#!/usr/bin/env bash
# Installed on the VPS as /usr/local/sbin/dental-legal-ai-deploy-gateway.
# It is the only command available to the GitHub Actions SSH key.
set -euo pipefail

readonly root_deployer="/usr/local/sbin/dental-legal-ai-deploy"
readonly requested_command="${SSH_ORIGINAL_COMMAND:-}"

if [[ "$requested_command" =~ ^(deploy|rollback)[[:space:]]([0-9a-f]{40})$ ]]; then
  exec sudo -n "$root_deployer" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
fi

echo "Only deploy <commit> or rollback <commit> is permitted." >&2
exit 64
