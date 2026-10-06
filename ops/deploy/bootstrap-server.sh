#!/usr/bin/env bash
# Run once as root from a reviewed checkout:
# bootstrap-server.sh '<GitHub Actions deploy public key>' '<read-only GitHub deploy private key path>'.
# It installs Docker from Docker's official APT repository and leaves the application disabled.
set -euo pipefail

readonly repository_url="git@github.com:Danil-super/Legal_ai.git"
readonly repository_dir="/srv/dental-legal-ai/repository"
readonly deploy_user="deploy"
readonly deploy_key="${1:-}"
readonly github_deploy_key_path="${2:-}"
readonly support_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly github_deploy_key="/etc/dental-legal-ai/github-deploy-readonly"
readonly github_known_hosts="/etc/dental-legal-ai/github_known_hosts"
readonly git_ssh_command="ssh -i ${github_deploy_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${github_known_hosts}"

if [[ "${EUID}" -ne 0 || "$#" -ne 2 || ! "$deploy_key" =~ ^ssh-ed25519[[:space:]] || ! -f "$github_deploy_key_path" ]]; then
  echo "Run as root with a GitHub Actions Ed25519 public key and a read-only GitHub deploy private key path." >&2
  exit 64
fi

# These utilities are also required when Docker was installed before bootstrap.
apt-get update
apt-get install --yes ca-certificates curl git python3 sudo openssh-client util-linux

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  source /etc/os-release
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  apt-get update
  apt-get install --yes docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

if ! id "$deploy_user" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$deploy_user"
fi
install -d -o "$deploy_user" -g "$deploy_user" -m 0700 "/home/$deploy_user/.ssh"
printf 'restrict,command="/usr/local/sbin/dental-legal-ai-deploy-gateway" %s\n' "$deploy_key" \
  >/home/$deploy_user/.ssh/authorized_keys
chown "$deploy_user:$deploy_user" /home/$deploy_user/.ssh/authorized_keys
chmod 0600 /home/$deploy_user/.ssh/authorized_keys

install -d -m 0750 /etc/dental-legal-ai
install -m 0600 "$github_deploy_key_path" "$github_deploy_key"
install -m 0644 "$support_dir/github_known_hosts" "$github_known_hosts"
# This network permits GitHub SSH only through GitHub's documented HTTPS port.
# Keep the configuration root-owned: all repository fetches run as root.
install -d -m 0700 /root/.ssh
cat >/root/.ssh/config <<'EOF'
Host github.com
  Hostname ssh.github.com
  Port 443
  User git
  HostKeyAlias github.com
EOF
chmod 0600 /root/.ssh/config

if [[ ! -d "$repository_dir/.git" ]]; then
  install -d -m 0755 "$(dirname "$repository_dir")"
  GIT_SSH_COMMAND="$git_ssh_command" git clone "$repository_url" "$repository_dir"
fi
git -C "$repository_dir" remote set-url origin "$repository_url"
GIT_SSH_COMMAND="$git_ssh_command" git -C "$repository_dir" fetch --prune origin "+refs/heads/main:refs/remotes/origin/main"
git -C "$repository_dir" checkout --detach --force origin/main

install -m 0755 "$support_dir/deploy-gateway.sh" /usr/local/sbin/dental-legal-ai-deploy-gateway
install -m 0750 "$support_dir/deploy-commit.sh" /usr/local/sbin/dental-legal-ai-deploy
printf '%s\n' "$deploy_user ALL=(root) NOPASSWD: /usr/local/sbin/dental-legal-ai-deploy *" \
  >/etc/sudoers.d/dental-legal-ai-deploy
chmod 0440 /etc/sudoers.d/dental-legal-ai-deploy
visudo --check --file=/etc/sudoers.d/dental-legal-ai-deploy

install -d -m 0750 /etc/dental-legal-ai /var/lib/dental-legal-ai
if [[ ! -f /etc/dental-legal-ai/app.env ]]; then
  install -m 0600 "$support_dir/production.env.example" /etc/dental-legal-ai/app.env
fi

echo "Bootstrap completed. Fill /etc/dental-legal-ai/app.env, then set DEPLOY_ENABLED=true in GitHub production variables."
