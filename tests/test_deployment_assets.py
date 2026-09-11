from __future__ import annotations

from pathlib import Path
from subprocess import run

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "ops" / "deploy"


def test_deployment_scripts_are_valid_bash() -> None:
    for name in ("bootstrap-server.sh", "deploy-gateway.sh", "deploy-commit.sh"):
        script = DEPLOY / name
        result = run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_deployment_accepts_only_main_ancestry_and_base_profile() -> None:
    script = (DEPLOY / "deploy-commit.sh").read_text(encoding="utf-8")

    assert 'merge-base --is-ancestor "$revision" origin/main' in script
    assert 'docker compose --project-name "$project_name" --env-file "$env_file"' in script
    assert "--profile analysis" not in script
    assert "--profile maintenance" not in script


def test_production_known_hosts_pins_the_vps_ed25519_key() -> None:
    known_hosts = DEPLOY / "known_hosts.production"
    result = run(
        ["ssh-keygen", "-F", "84.201.153.147", "-f", str(known_hosts)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ssh-ed25519" in result.stdout


def test_private_repository_fetch_uses_a_dedicated_read_only_deploy_key() -> None:
    deploy_script = (DEPLOY / "deploy-commit.sh").read_text(encoding="utf-8")
    bootstrap_script = (DEPLOY / "bootstrap-server.sh").read_text(encoding="utf-8")
    github_known_hosts = DEPLOY / "github_known_hosts"

    assert 'readonly repository_url="git@github.com:Danil-super/Legal_ai.git"' in deploy_script
    assert (
        'readonly github_deploy_key="/etc/dental-legal-ai/github-deploy-readonly"'
        in deploy_script
    )
    assert "StrictHostKeyChecking=yes" in deploy_script
    assert 'GIT_SSH_COMMAND="$git_ssh_command" git -C "$repository_dir" fetch' in deploy_script
    assert 'install -m 0600 "$github_deploy_key_path" "$github_deploy_key"' in bootstrap_script
    assert 'GIT_SSH_COMMAND="$git_ssh_command" git clone "$repository_url"' in bootstrap_script

    result = run(
        ["ssh-keygen", "-F", "github.com", "-f", str(github_known_hosts)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ssh-ed25519" in result.stdout


def test_production_gate_is_evaluated_after_environment_binding() -> None:
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    rollback_workflow = (ROOT / ".github" / "workflows" / "rollback.yml").read_text(
        encoding="utf-8"
    )

    ci_deploy_job = ci_workflow.split("  deploy-production:", maxsplit=1)[1]
    rollback_job = rollback_workflow.split("  rollback-production:", maxsplit=1)[1]

    assert "vars.DEPLOY_ENABLED == 'true'" not in ci_deploy_job.split(
        "    environment:", maxsplit=1
    )[0]
    assert "vars.DEPLOY_ENABLED == 'true'" not in rollback_job.split(
        "    environment:", maxsplit=1
    )[0]
    assert "      if: vars.DEPLOY_ENABLED == 'true'" in ci_workflow
    assert "      if: vars.DEPLOY_ENABLED == 'true'" in rollback_workflow


def test_deploy_workflows_allow_the_explicit_ssh_agent_key() -> None:
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    rollback_workflow = (ROOT / ".github" / "workflows" / "rollback.yml").read_text(
        encoding="utf-8"
    )

    assert "ssh-add - <<<\"$DEPLOY_SSH_PRIVATE_KEY\"" in ci_workflow
    assert "ssh -o BatchMode=yes -o IdentitiesOnly=no deploy@" in ci_workflow
    assert "ssh-add - <<<\"$DEPLOY_SSH_PRIVATE_KEY\"" in rollback_workflow
    assert "ssh -o BatchMode=yes -o IdentitiesOnly=no deploy@" in rollback_workflow


def test_telegram_gateway_image_includes_its_legal_core_pseudonymization_dependency() -> None:
    dockerfile = (ROOT / "services" / "gateway" / "telegram" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "/app/services/legal_core/src" in dockerfile
    assert "COPY services/legal_core/src/legal_core" in dockerfile


def test_agent_orchestrator_is_not_exposed_to_the_edge_network() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    service = compose.split("  agent-orchestrator:\n", maxsplit=1)[1].split(
        "\n  legal-watcher:", maxsplit=1
    )[0]

    assert "networks: [backend]" in service
    assert "edge" not in service


def test_tool_free_hermes_profiles_bypass_the_upstream_s6_entrypoint() -> None:
    compose = (ROOT / "ops" / "hermes" / "docker-compose.hermes.yml").read_text(
        encoding="utf-8"
    )

    for service_name, next_service in (
        ("hermes-researcher", "hermes-reviewer"),
        ("hermes-reviewer", "agent-orchestrator"),
    ):
        service = compose.split(f"  {service_name}:\n", maxsplit=1)[1].split(
            f"\n  {next_service}:", maxsplit=1
        )[0]

        assert 'user: "10000:10000"' in service
        assert (
            "entrypoint:\n"
            "      - /bin/sh\n"
            "      - -lc\n"
            "      - >-\n"
            "        /opt/hermes/.venv/bin/python /opt/legal/assert_tool_free.py &&\n"
            "        exec /opt/hermes/.venv/bin/hermes gateway run --no-supervise"
        ) in service
        assert "\n    command:" not in service
        assert "PYTHONPATH: /opt/hermes" in service
        assert "no-new-privileges:true" in service
        assert "cap_drop:\n      - ALL" in service


def test_hermes_legal_profile_uses_the_explicit_custom_provider_contract() -> None:
    profile = (ROOT / "ops" / "hermes" / "legal-profile.config.yaml").read_text(
        encoding="utf-8"
    )

    assert (
        "model:\n"
        "  provider: custom\n"
        "  base_url: ${OPENAI_BASE_URL}\n"
        "  api_key: ${OPENAI_API_KEY}"
    ) in profile
    assert "  model: ${HERMES_MODEL}" not in profile


def test_legal_watch_services_pass_required_arguments_to_their_modules() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    watcher = compose.split("  legal-watcher:\n", maxsplit=1)[1].split(
        "\n  legal-watch-importer:", maxsplit=1
    )[0]
    importer = compose.split("  legal-watch-importer:\n", maxsplit=1)[1].split(
        "\n  telegram-gateway:", maxsplit=1
    )[0]

    continuation = chr(92) + "\n"
    assert (
        "python -m legal_core.legal_watcher "
        f"{continuation}          --rules "
        f"/app/services/legal_core/corpus/legal_watch_rules.v1.json {continuation}"
        f"          --inbox /var/lib/dental-legal-ai/legal-update-inbox {continuation}"
        f"          --publication-from $$watch_from {continuation}"
        "          --publication-to $$watch_to;"
    ) in watcher
    assert (
        "python -m legal_core.legal_watch_importer "
        f"{continuation}          --inbox /var/lib/dental-legal-ai/legal-update-inbox "
        f"{continuation}          --max-candidates 500;"
    ) in importer
    assert "if python -m legal_core.legal_watcher " in watcher
    assert "legal watcher run failed; retrying after delay" in watcher
    assert "if python -m legal_core.legal_watch_importer " in importer
    assert "legal watch import run failed; retrying after delay" in importer


def test_production_legal_watcher_uses_only_the_internal_vpn_proxy() -> None:
    base_compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    production_compose = (
        ROOT / "ops" / "deploy" / "docker-compose.production.yml"
    ).read_text(encoding="utf-8")
    base_watcher = base_compose.split("  legal-watcher:\n", maxsplit=1)[1].split(
        "\n  legal-watch-importer:", maxsplit=1
    )[0]
    watcher = production_compose.split("  legal-watcher:\n", maxsplit=1)[1].split(
        "\n  legal-watch-importer:", maxsplit=1
    )[0]

    assert "LEGAL_WATCH_PROXY_URL: http://telegram-vpn-proxy:8080" in watcher
    assert "networks: [edge]" in base_watcher
