from __future__ import annotations

import json
import sys
from pathlib import Path
from shlex import quote
from subprocess import run

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "ops" / "deploy"


def test_deployment_scripts_are_valid_bash() -> None:
    for name in ("bootstrap-server.sh", "deploy-gateway.sh", "deploy-commit.sh"):
        script = DEPLOY / name
        result = run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_deployment_accepts_only_main_ancestry_and_explicit_analysis_mode() -> None:
    script = (DEPLOY / "deploy-commit.sh").read_text(encoding="utf-8")

    assert 'merge-base --is-ancestor "$revision" origin/main' in script
    assert (
        'compose_args=(--parallel 1 --project-name "$project_name" --env-file "$env_file"'
        in script
    )
    assert 'if [[ "$analysis_enabled" == 1 ]]; then' in script
    assert "ops/hermes/docker-compose.hermes.yml --profile analysis" in script
    assert "--parallel 1" in script
    assert "--profile maintenance" not in script


def test_production_overlay_disables_the_complete_analysis_runtime() -> None:
    production = (DEPLOY / "docker-compose.production.yml").read_text(encoding="utf-8")

    assert production.count('AGENT_ORCHESTRATOR_URL: ""') == 2
    assert production.count('AGENT_INTERNAL_KEY: ""') == 2


def analysis_compose_fixture() -> dict:
    image = "dental-legal-hermes:5fc308a70719a83cccdbba4c0e39c23f5a8239d5"
    research_key, review_key, agent_key = "r" * 32, "v" * 32, "a" * 32
    return {"services": {
        "minio": {"image": "dental-legal-minio:" + "a" * 40},
        "legal-core": {"environment": {
            "AGENT_INTERNAL_KEY": agent_key,
            "AGENT_ORCHESTRATOR_URL": "http://agent-orchestrator:8010",
        }},
        "hermes-researcher": {
            "image": image, "pull_policy": "never", "environment": {
                "API_SERVER_KEY": research_key,
                "OPENAI_BASE_URL": "https://synthetic.example.invalid/v1",
                "OPENAI_API_KEY": "synthetic-provider-key",
                "HERMES_MODEL": "synthetic-researcher",
            },
        },
        "hermes-reviewer": {
            "image": image, "pull_policy": "never", "environment": {
                "API_SERVER_KEY": review_key,
                "OPENAI_BASE_URL": "https://synthetic.example.invalid/v1",
                "OPENAI_API_KEY": "synthetic-provider-key",
                "HERMES_MODEL": "synthetic-reviewer",
            },
        },
        "agent-orchestrator": {"environment": {
            "AGENT_INTERNAL_KEY": agent_key,
            "HERMES_RESEARCHER_URL": "http://hermes-researcher:8642",
            "HERMES_REVIEWER_URL": "http://hermes-reviewer:8642",
            "HERMES_RESEARCHER_API_KEY": research_key,
            "HERMES_REVIEWER_API_KEY": review_key,
        }},
        "telegram-gateway": {"environment": {
            "AGENT_INTERNAL_KEY": agent_key,
            "AGENT_ORCHESTRATOR_URL": "http://agent-orchestrator:8010",
        }},
    }}


@pytest.mark.parametrize(
    ("flag", "config_exit", "stack_exit", "image_exit", "expected_exit"),
    [
        ("", 0, 0, 0, 0),
        ("DEPLOY_ANALYSIS_ENABLED=0", 0, 1, 0, 1),
        ("DEPLOY_ANALYSIS_ENABLED=0", 1, 0, 0, 1),
        ("DEPLOY_ANALYSIS_ENABLED=1", 0, 0, 0, 0),
        ("DEPLOY_ANALYSIS_ENABLED=1", 0, 0, 1, 1),
        ("DEPLOY_ANALYSIS_ENABLED=1", 0, 1, 0, 1),
        ("DEPLOY_ANALYSIS_ENABLED=1\nDEPLOY_ANALYSIS_ENABLED=0", 0, 0, 0, 64),
        ("DEPLOY_ANALYSIS_ENABLED=SENTINEL_SECRET", 0, 0, 0, 64),
    ],
)
def test_deploy_records_success_only_when_the_selected_stack_is_ready(
    tmp_path: Path, flag: str, config_exit: int, stack_exit: int,
    image_exit: int, expected_exit: int,
) -> None:
    """Run real mode selection and preflight with local Docker and syslog stubs."""
    command_log = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/bash\n'
        'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [[ "$*" == *"image inspect"* ]]; then\n'
        '  echo SENTINEL_SECRET >&2; exit "$IMAGE_EXIT"\n'
        'fi\n'
        'if [[ "$*" == *"config --format json"* ]]; then\n'
        '  printf "%s" "$COMPOSE_JSON"; exit 0\n'
        'fi\n'
        'if [[ "$*" == *" build "* ]]; then exit 0; fi\n'
        'for arg in "$@"; do\n'
        '  if [[ "$arg" == config ]]; then\n'
        '    echo SENTINEL_SECRET >&2; exit "$CONFIG_EXIT"\n'
        '  fi\n'
        '  if [[ "$arg" == --wait ]]; then exit "$STACK_EXIT"; fi\n'
        'done\nexit 99\n',
        encoding="utf-8",
    )
    docker.chmod(0o700)
    (tmp_path / "python3").symlink_to(sys.executable)
    logger = tmp_path / "logger"
    logger.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    logger.chmod(0o700)
    env_file = tmp_path / "app.env"
    env_file.write_text(flag + "\n", encoding="utf-8")
    revision, previous = "a" * 40, "b" * 40
    state_file = tmp_path / "last-successful-revision"
    state_file.write_text(previous + "\n", encoding="utf-8")
    source = (DEPLOY / "deploy-commit.sh").read_text(encoding="utf-8")
    gate = source[source.index("# Select the persistent server mode") :]
    script = tmp_path / "readiness.sh"
    script.write_text(
        "set -euo pipefail\n"
        "readonly project_name=synthetic-deploy operation=deploy\n"
        f"readonly env_file={quote(str(env_file))}\n"
        f"readonly state_dir={quote(str(tmp_path))} revision={quote(revision)}\n"
        + gate,
        encoding="utf-8",
    )
    result = run(
        ["/bin/bash", str(script)], cwd=ROOT, capture_output=True,
        text=True, check=False, timeout=5,
        env={
            "PATH": str(tmp_path), "COMMAND_LOG": str(command_log),
            "CONFIG_EXIT": str(config_exit), "STACK_EXIT": str(stack_exit),
            "IMAGE_EXIT": str(image_exit),
            "COMPOSE_JSON": json.dumps(analysis_compose_fixture()),
        },
    )
    assert result.returncode == expected_exit, result.stderr
    assert "SENTINEL_SECRET" not in result.stdout + result.stderr
    assert state_file.read_text(encoding="utf-8").strip() == (
        revision if expected_exit == 0 else previous
    )
    if expected_exit == 64:
        assert not command_log.exists()
        return
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert "config --quiet" in commands[0]
    assert ("--profile analysis" in commands[0]) == (flag == "DEPLOY_ANALYSIS_ENABLED=1")
    if config_exit or image_exit:
        assert not any("--no-build" in command for command in commands)
    else:
        assert any(" build " in f" {command} " for command in commands)
        assert "up --build" not in commands[-1]
        assert "--no-build --detach --remove-orphans --wait --wait-timeout 180" in commands[-1]


@pytest.mark.parametrize("fault", ["missing_key", "reused_key", "bad_url", "wrong_gateway", "json"])
def test_analysis_preflight_rejects_bad_configuration_without_exposing_secrets(fault: str) -> None:
    document = analysis_compose_fixture()
    services = document["services"]
    sentinel = "SENTINEL_SECRET"
    if fault == "missing_key":
        services["hermes-researcher"]["environment"]["OPENAI_API_KEY"] = ""
    elif fault == "reused_key":
        services["hermes-reviewer"]["environment"]["API_SERVER_KEY"] = "r" * 32
    elif fault == "bad_url":
        for name in ("hermes-researcher", "hermes-reviewer"):
            services[name]["environment"]["OPENAI_BASE_URL"] = f"https://user:{sentinel}@host/v1"
    elif fault == "wrong_gateway":
        services["telegram-gateway"]["environment"]["AGENT_ORCHESTRATOR_URL"] = sentinel
    body = sentinel if fault == "json" else json.dumps(document)
    result = run(
        [sys.executable, str(DEPLOY / "analysis-preflight.py")], input=body,
        capture_output=True, text=True, check=False, timeout=5, env={},
    )
    assert result.returncode == 1
    assert sentinel not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_ci_uses_the_production_python_and_hashed_lockfile() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "services" / "legal_core" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    python_version = dockerfile.split("FROM python:", maxsplit=1)[1].split("-", maxsplit=1)[0]
    assert workflow.count(f"python-version: '{python_version}'") == 2
    assert workflow.count("python -m pip install --require-hashes -r requirements.lock") == 2


def test_minio_ci_and_runtime_build_the_same_pinned_security_release() -> None:
    commit = "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a"
    image = f"dental-legal-minio:{commit}"
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "ops" / "minio" / "Dockerfile").read_text(encoding="utf-8")
    assert image in compose
    assert workflow.count(image) == 2
    assert "dockerfile: ops/minio/Dockerfile" in compose
    assert "docker build --file ops/minio/Dockerfile" in workflow
    assert "FROM golang:1.24.8-bookworm AS build" in dockerfile
    assert f"git fetch --depth 1 origin {commit}" in dockerfile
    assert f'test "$(git rev-parse HEAD)" = {commit}' in dockerfile
    assert "go mod download && go mod verify" in dockerfile
    assert "GOTOOLCHAIN=local" in dockerfile
    assert "GOFLAGS=-mod=readonly" in dockerfile
    assert "buildscripts/gen-ldflags.go 2025-10-15T17:29:55Z" in dockerfile
    assert "ca-certificates curl" in dockerfile
    assert "FROM minio/minio" not in dockerfile
    assert "quay.io/minio/minio@" not in compose + workflow
    assert "minio/minio:RELEASE.2025-04-22T22-12-26Z" not in compose + workflow


def test_production_deploy_reuses_a_prebuilt_checked_minio_security_image() -> None:
    script = (DEPLOY / "deploy-commit.sh").read_text(encoding="utf-8")

    assert 're.fullmatch(r"dental-legal-minio:[0-9a-f]{40}", image)' in script
    assert "readonly minio_image" in script
    assert 'docker image inspect "$minio_image"' in script
    assert 'build "${build_services[@]}"' in script
    assert "up --no-build --detach --remove-orphans --wait --wait-timeout 180" in script
    assert "\n  up --build " not in script


def test_alembic_accepts_reserved_characters_in_generated_passwords() -> None:
    result = run(
        # Later migrations inspect the live database and cannot render offline.
        [sys.executable, "-m", "alembic", "upgrade", "8b1773dcd131", "--sql"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={
            "POSTGRES_HOST": "localhost",
            "POSTGRES_DB": "synthetic_offline_migration",
            "POSTGRES_USER": "synthetic_owner",
            "POSTGRES_PASSWORD": "synthetic-only:p@ss/with%reserved?characters#",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE" in result.stdout


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
    assert "ssh -o BatchMode=yes -o IdentitiesOnly=no" in ci_workflow
    assert "ssh-add - <<<\"$DEPLOY_SSH_PRIVATE_KEY\"" in rollback_workflow
    assert "ssh -o BatchMode=yes -o IdentitiesOnly=no" in rollback_workflow


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
            "        /opt/hermes/.venv/bin/python /opt/legal/render_profile.py\n"
            "        /opt/legal/legal-profile.config.yaml /opt/data/config.yaml &&\n"
            "        /opt/hermes/.venv/bin/python /opt/legal/assert_tool_free.py &&\n"
            "        exec /opt/hermes/.venv/bin/hermes gateway run --no-supervise"
        ) in service
        assert "\n    command:" not in service
        assert "PYTHONPATH: /opt/hermes" in service
        assert (
            "./ops/hermes/legal-profile.config.yaml:/opt/legal/legal-profile.config.yaml:ro"
            in service
        )
        assert "./ops/hermes/render_profile.py:/opt/legal/render_profile.py:ro" in service
        assert ":/opt/data/config.yaml:ro" not in service
        assert "no-new-privileges:true" in service
        assert "cap_drop:\n      - ALL" in service


def test_hermes_legal_profile_uses_the_explicit_custom_provider_contract() -> None:
    profile = (ROOT / "ops" / "hermes" / "legal-profile.config.yaml").read_text(
        encoding="utf-8"
    )

    assert (
        "model:\n"
        "  provider: custom\n"
        "  default: ${HERMES_MODEL}\n"
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
