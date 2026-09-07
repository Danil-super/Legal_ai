from __future__ import annotations

from pathlib import Path
from subprocess import run

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "ops" / "deploy"


def test_deployment_scripts_are_valid_bash() -> None:
    for name in ("deploy-gateway.sh", "deploy-commit.sh"):
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
