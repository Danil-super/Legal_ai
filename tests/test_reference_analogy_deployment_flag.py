"""Resolve real Compose overlays without a daemon, secrets, network calls or deployment."""

import json
import os
from pathlib import Path
from subprocess import run

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("overlays", [(), ("ops/deploy/docker-compose.production.yml",),
    ("ops/deploy/docker-compose.production.yml", "ops/hermes/docker-compose.hermes.yml")])
@pytest.mark.parametrize("value,expected", [(None, "0"), ("", "0"), ("0", "0"), ("1", "1"),
                                           ("invalid", "invalid")])
def test_reference_analogy_flag_reaches_only_core_through_actual_compose_resolution(
    overlays, value, expected,
):
    environment = {
        "PATH": os.defpath,
        "POSTGRES_PASSWORD": "synthetic-password",
        "POSTGRES_APP_PASSWORD": "synthetic-app-password",
        "MINIO_ROOT_PASSWORD": "synthetic-storage-password",
        "TELEGRAM_BOT_TOKEN": "synthetic-token",
        "AGENT_INTERNAL_KEY": "synthetic-agent-key",
        "HERMES_LLM_BASE_URL": "https://synthetic.invalid/v1",
        "HERMES_LLM_API_KEY": "synthetic-provider-key",
        "HERMES_RESEARCHER_LLM_MODEL": "synthetic-researcher",
        "HERMES_REVIEWER_LLM_MODEL": "synthetic-reviewer",
        "HERMES_RESEARCHER_API_KEY": "synthetic-researcher-key",
        "HERMES_REVIEWER_API_KEY": "synthetic-reviewer-key",
    }
    if value is not None:
        environment["AI_FIRST_REFERENCE_ANALOGIES_ENABLED"] = value
    arguments = ["docker", "compose", "--env-file", os.devnull,
                 "-f", str(ROOT / "docker-compose.yml")]
    for overlay in overlays:
        arguments.extend(["-f", str(ROOT / overlay)])
    if "ops/hermes/docker-compose.hermes.yml" in overlays:
        arguments.extend(["--profile", "analysis"])
    result = run([*arguments, "config", "--format", "json"], env=environment,
                 cwd=ROOT, check=False, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert services["legal-core"]["environment"]["AI_FIRST_REFERENCE_ANALOGIES_ENABLED"] == expected
    assert all("AI_FIRST_REFERENCE_ANALOGIES_ENABLED" not in service.get("environment", {})
               for name, service in services.items() if name != "legal-core")
    assert not any("personal" in name for name in services)


@pytest.mark.parametrize("template", [".env.example", "ops/deploy/production.env.example"])
def test_reference_analogy_templates_default_to_disabled(template):
    entries = [line for line in (ROOT / template).read_text().splitlines()
               if line.startswith("AI_FIRST_REFERENCE_ANALOGIES_ENABLED=")]
    assert entries == ["AI_FIRST_REFERENCE_ANALOGIES_ENABLED=0"]
